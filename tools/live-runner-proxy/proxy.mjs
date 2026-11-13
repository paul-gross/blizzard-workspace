#!/usr/bin/env node
/**
 * Live runner auth proxy.
 *
 * The live runner host completes a browser sign-in only on an origin it declares (`public_url`), so a
 * feature env's dev server, each on its own port, can never finish a login against it. This proxy is
 * that one declared origin, `http://localhost:<BZ_LIVE_RUNNER_PROXY_PORT>`. It forwards every request
 * to the live runner as though it arrived there, keeps the session cookie the login callback mints,
 * and attaches it to every later request, so any number of dev servers share one live session.
 *
 * Because it attaches a live session to whatever reaches it, it serves loopback callers only: a
 * non-loopback Host (DNS rebinding) or a non-loopback Origin (a cross-site form or fetch) is refused
 * before anything is forwarded. The login callback is the one cross-site request it admits, the hub's
 * `form_post`, and that request never carries the stored session.
 *
 * It never exits on bad or missing configuration. It idles without binding instead, so the workspace
 * scope it runs in always comes up, including on the machine that hosts the live runner itself.
 *
 * Node standard library only; no install step. Setup and use: ./README.md.
 */

import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import os from 'node:os';
import path from 'node:path';
import { pipeline } from 'node:stream';

const COOKIE = {
  sessionPrefix: 'bz_runner_session_',
  bounceStatePrefix: 'bz_runner_bounce_state_',
};
const ROUTE = {
  login: '/api/auth/login',
  callback: '/api/auth/callback',
  status: '/__proxy',
  forget: '/__proxy/forget',
};
const LOOPBACK_HOSTNAMES = new Set(['localhost', '127.0.0.1', '[::1]']);
const HOP_BY_HOP_HEADERS = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade',
]);
const LOGIN_RETURN_TTL_MS = 15 * 60 * 1000;

function main() {
  process.on('uncaughtException', (err) => log(`uncaught: ${err.stack ?? err}`));

  const config = loadConfig();
  if (config.problem) {
    idle(config.problem);
    return;
  }
  startProxy(config);
}

// --- configuration -------------------------------------------------------------------------------

/**
 * @returns {{ upstream: URL, port: number, stateFile: string } | { problem: string }}
 */
function loadConfig() {
  const upstreamFile = path.join(path.dirname(new URL(import.meta.url).pathname), 'upstream.local');
  const stateFile =
    process.env.BZ_LIVE_RUNNER_SESSION_FILE ||
    path.join(process.env.XDG_STATE_HOME || path.join(os.homedir(), '.local', 'state'), 'blizzard-live-runner-proxy', 'session.json');

  const port = Number(process.env.BZ_LIVE_RUNNER_PROXY_PORT);
  if (!Number.isInteger(port) || port <= 0) {
    return { problem: `BZ_LIVE_RUNNER_PROXY_PORT is not a port (${process.env.BZ_LIVE_RUNNER_PROXY_PORT})` };
  }

  let raw;
  try {
    raw = process.env.BZ_LIVE_RUNNER_URL?.trim() || readFirstSetting(upstreamFile);
  } catch (err) {
    return { problem: `cannot read ${upstreamFile}: ${err.message}` };
  }
  if (!raw) return { problem: `no upstream; put the live runner's URL in ${upstreamFile} (see README)` };

  const upstream = URL.canParse(raw) ? new URL(raw) : null;
  if (!upstream || !['http:', 'https:'].includes(upstream.protocol)) {
    return { problem: `upstream ${JSON.stringify(raw)} is not an http(s) URL` };
  }
  return { upstream, port, stateFile };
}

/** The first non-blank, non-comment line of `file`, or '' when the file does not exist. */
function readFirstSetting(file) {
  if (!fs.existsSync(file)) return '';
  const line = fs
    .readFileSync(file, 'utf8')
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l && !l.startsWith('#'));
  return line ?? '';
}

// --- the stored session --------------------------------------------------------------------------

/**
 * The runner session cookie(s), persisted to one file (mode 600) so a restart keeps the sign-in.
 * Every read goes to disk, and every write re-reads first, so concurrent responses never overwrite
 * each other's update.
 */
class SessionStore {
  /** @param {string} file */
  constructor(file) {
    this.file = file;
  }

  /** @returns {Record<string, { value: string, expires: number | null }>} the unexpired cookies */
  read() {
    let jar;
    try {
      jar = JSON.parse(fs.readFileSync(this.file, 'utf8'));
    } catch {
      return {};
    }
    const now = Date.now();
    return Object.fromEntries(Object.entries(jar).filter(([, c]) => c.expires === null || c.expires > now));
  }

  /** Fold the session cookies among an upstream response's Set-Cookies into the store. */
  absorb(setCookies) {
    const updates = setCookies.filter((c) => c.name.startsWith(COOKIE.sessionPrefix));
    if (updates.length === 0) return;

    const jar = this.read();
    for (const { name, value, expires } of updates) {
      if (expires === 0) {
        delete jar[name];
        log(`session cleared: ${name}`);
      } else {
        jar[name] = { value, expires };
        log(`session stored: ${name}${expires ? ` (expires ${new Date(expires).toISOString()})` : ''}`);
      }
    }
    this.write(jar);
  }

  clear() {
    this.write({});
  }

  write(jar) {
    fs.mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
    const tmp = `${this.file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(jar, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, this.file);
  }
}

// --- cookies -------------------------------------------------------------------------------------

/**
 * @param {string[] | undefined} lines Set-Cookie header values
 * @returns {{ name: string, value: string, expires: number | null }[]} expires: epoch ms, null for a
 *   browser-session cookie, 0 for a deletion
 */
function parseSetCookies(lines = []) {
  return lines.map((line) => {
    const [pair, ...attributes] = line.split(';');
    const { name, value } = splitPair(pair);

    let expires = null;
    for (const attribute of attributes) {
      const { name: key, value: v } = splitPair(attribute);
      if (key.toLowerCase() === 'max-age') expires = Date.now() + Number(v) * 1000;
      else if (key.toLowerCase() === 'expires' && expires === null) expires = Date.parse(v) || null;
    }
    const deleted = !value || (expires !== null && expires <= Date.now());
    return { name, value, expires: deleted ? 0 : expires };
  });
}

/** @returns {{ name: string, value: string }[]} the pairs of a Cookie request header */
function parseCookieHeader(header = '') {
  return header
    .split(';')
    .map((part) => part.trim())
    .filter(Boolean)
    .map(splitPair);
}

/**
 * The browser's cookies with the stored session in place of any same-named cookie it sent: every
 * login went through this proxy, so the stored copy is never the older one.
 */
function cookieHeaderWithSession(browserHeader, jar) {
  const browserCookies = parseCookieHeader(browserHeader).filter(({ name }) => !(name in jar));
  const sessionCookies = Object.entries(jar).map(([name, { value }]) => ({ name, value }));
  return [...browserCookies, ...sessionCookies].map(({ name, value }) => `${name}=${value}`).join('; ');
}

function splitPair(text) {
  const eq = text.indexOf('=');
  if (eq < 0) return { name: text.trim(), value: '' };
  return { name: text.slice(0, eq).trim(), value: text.slice(eq + 1).trim().replace(/^"(.*)"$/, '$1') };
}

// --- who may call it -----------------------------------------------------------------------------

/** The origin of `href` when it is a loopback http(s) origin, such as a dev server's; otherwise null. */
function loopbackOrigin(href) {
  if (!href || !URL.canParse(href)) return null;
  const url = new URL(href);
  const isWeb = url.protocol === 'http:' || url.protocol === 'https:';
  return isWeb && LOOPBACK_HOSTNAMES.has(url.hostname) ? url.origin : null;
}

/**
 * Why `req` must be refused, or null to admit it. Host must name loopback at any port, because each
 * dev server forwards with its own Host. Origin, when sent, must be loopback too, except on the login
 * callback, which the hub posts cross-site.
 */
function refusalReason(req, pathname) {
  const host = req.headers.host ?? '';
  if (!loopbackOrigin(`http://${host}`)) return `Host ${host || '(none)'} is not loopback`;

  const origin = req.headers.origin;
  if (origin && pathname !== ROUTE.callback && !loopbackOrigin(origin)) return `Origin ${origin} is not loopback`;

  return null;
}

// --- returning a finished login to its dev server ------------------------------------------------

/**
 * Login bounce state -> the dev server origin the login began on. The runner sends a finished login
 * to a path, which would land on this proxy; this puts the browser back on the dev server instead.
 * Keyed by the bounce state, so concurrent logins from different envs each return home.
 */
class LoginReturns {
  #origins = new Map();

  /** Record where a login began, from the login response's bounce-state cookie and the request's Referer. */
  remember(loginReq, setCookies, ownOrigin) {
    const from = loopbackOrigin(loginReq.headers.referer);
    const state = setCookies.find((c) => c.name.startsWith(COOKIE.bounceStatePrefix) && c.expires !== 0)?.value;
    if (!from || from === ownOrigin || !state) return;

    this.#origins.set(state, from);
    setTimeout(() => this.#origins.delete(state), LOGIN_RETURN_TTL_MS).unref();
  }

  /** The callback's relative redirect made absolute on the dev server the login began on, when known. */
  resolve(callbackReq, location) {
    if (!location?.startsWith('/')) return location;

    const state = parseCookieHeader(callbackReq.headers.cookie).find(({ name }) =>
      name.startsWith(COOKIE.bounceStatePrefix),
    )?.value;
    const from = state && this.#origins.get(state);
    if (!from) return location;

    this.#origins.delete(state);
    return from + location;
  }
}

// --- the proxy -----------------------------------------------------------------------------------

function startProxy({ upstream, port, stateFile }) {
  const ownHost = `localhost:${port}`;
  const ownOrigin = `http://${ownHost}`;
  const sessions = new SessionStore(stateFile);
  const loginReturns = new LoginReturns();
  const transport = upstream.protocol === 'https:' ? https : http;

  const server = http.createServer((req, res) => {
    const pathname = URL.canParse(req.url, ownOrigin) ? new URL(req.url, ownOrigin).pathname : null;
    if (pathname === null) return reply(res, 400, 'bad request target');

    const refusal = refusalReason(req, pathname);
    if (refusal) {
      log(`refused ${req.method} ${pathname}: ${refusal}`);
      return reply(res, 403, refusal);
    }

    if (pathname === ROUTE.status || pathname === `${ROUTE.status}/`) return replyStatus(res);
    if (pathname === ROUTE.forget && req.method === 'POST') {
      sessions.clear();
      log('session forgotten on request');
      return res.writeHead(204).end();
    }

    forward(req, res, pathname);
  });

  /** Send `req` to the live runner as though it arrived there, and stream the answer back. */
  function forward(req, res, pathname) {
    const isCallback = pathname === ROUTE.callback;
    const upstreamReq = transport.request(
      {
        protocol: upstream.protocol,
        hostname: upstream.hostname,
        port: upstream.port || (upstream.protocol === 'https:' ? 443 : 80),
        servername: upstream.hostname, // TLS SNI and the certificate check stay on the real name
        method: req.method,
        path: req.url,
        headers: upstreamHeaders(req, isCallback),
      },
      (upstreamRes) => relay(req, res, pathname, upstreamRes),
    );
    upstreamReq.setTimeout(0);
    upstreamReq.on('error', (err) => {
      log(`upstream error ${req.method} ${pathname}: ${err.message}`);
      if (res.headersSent) res.destroy();
      else reply(res, 502, `upstream ${upstream.origin} unreachable: ${err.message}`);
    });
    res.on('close', () => upstreamReq.destroy());
    req.pipe(upstreamReq);
  }

  function upstreamHeaders(req, isCallback) {
    const headers = withoutHopByHop(req.headers);
    // The runner picks its sign-in origin by Host, so every request arrives as this proxy's origin.
    headers.host = ownHost;
    // The callback runs on the browser's own bounce cookies; it never needs, or gets, the stored session.
    const cookie = isCallback ? req.headers.cookie : cookieHeaderWithSession(req.headers.cookie, sessions.read());
    if (cookie) headers.cookie = cookie;
    else delete headers.cookie;
    return headers;
  }

  function relay(req, res, pathname, upstreamRes) {
    const setCookies = parseSetCookies(upstreamRes.headers['set-cookie']);
    try {
      sessions.absorb(setCookies);
    } catch (err) {
      log(`could not save the session: ${err.message}`);
    }

    const headers = withoutHopByHop(upstreamRes.headers);
    if (pathname === ROUTE.login) loginReturns.remember(req, setCookies, ownOrigin);
    if (pathname === ROUTE.callback) headers.location = loginReturns.resolve(req, headers.location);
    if (headers.location === undefined) delete headers.location;

    res.writeHead(upstreamRes.statusCode ?? 502, headers);
    res.flushHeaders(); // SSE: headers out before the first event
    // pipeline tears down both sides when either ends or fails, so an upstream drop closes the
    // browser's stream (and EventSource reconnects) rather than leaving it open and silent.
    pipeline(upstreamRes, res, (err) => {
      if (err && err.code !== 'ERR_STREAM_PREMATURE_CLOSE') log(`stream ${req.method} ${pathname}: ${err.message}`);
    });
  }

  function replyStatus(res) {
    const jar = sessions.read();
    const body = {
      upstream: upstream.origin,
      origin: ownOrigin,
      login: `${ownOrigin}${ROUTE.login}`,
      sessionFile: stateFile,
      sessions: Object.fromEntries(
        Object.entries(jar).map(([name, c]) => [name, { expires: c.expires ? new Date(c.expires).toISOString() : null }]),
      ),
    };
    res.writeHead(200, { 'content-type': 'application/json' }).end(`${JSON.stringify(body, null, 2)}\n`);
  }

  server.requestTimeout = 0; // SSE streams stay open indefinitely
  server.on('error', (err) => {
    server.close();
    idle(`cannot listen on ${ownOrigin}: ${err.message}`);
  });
  server.listen(port, '127.0.0.1', () => {
    log(`live runner proxy: ${ownOrigin} -> ${upstream.origin} (session file ${stateFile})`);
    const held = Object.keys(sessions.read());
    log(held.length > 0 ? `holding session: ${held.join(', ')}` : `no session yet; sign in at ${ownOrigin}${ROUTE.login}`);
  });
}

// --- helpers -------------------------------------------------------------------------------------

function withoutHopByHop(headers) {
  return Object.fromEntries(Object.entries(headers).filter(([name]) => !HOP_BY_HOP_HEADERS.has(name)));
}

function reply(res, status, message) {
  res.writeHead(status, { 'content-type': 'text/plain' }).end(`live runner proxy: ${message}\n`);
}

/** Stay alive without binding, so the service never restart-loops the workspace scope. */
function idle(reason) {
  log(`${reason}; idling without binding.`);
  setInterval(() => {}, 1 << 30);
}

function log(message) {
  console.log(`${new Date().toISOString()} ${message}`);
}

// Last, so every class above is initialized before it runs.
main();
