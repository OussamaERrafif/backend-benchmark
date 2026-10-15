import cluster from 'node:cluster';
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire('/app/package.json');
const workers = Number(process.env.APP_WORKERS || 1);
if (cluster.isPrimary) {
  for (let i = 0; i < workers; i++) cluster.fork();
  // A worker crash fails the container instead of being hidden by automatic restart.
  cluster.on('exit', () => process.exit(1));
} else {
  const { Pool, types } = require('pg');
  types.setTypeParser(20, Number);
  const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: Math.max(1, Math.floor(Number(process.env.DB_POOL_SIZE || 20) / workers)), min: 0 });
  const fixture = JSON.parse(fs.readFileSync('/fixtures/large.json', 'utf8'));
  const compute = () => { let a = 0; for (let i = 1; i <= 100000; i++) a = (a + ((i * i) % 97)) % 1000000007; return { result: a }; };
  const invalid = () => ({ error: 'invalid request' });
  const validId = (v) => /^\d+$/.test(String(v)) && Number(v) > 0 && Number.isSafeInteger(Number(v));
  const routes = [
    ['get', '/health', async () => ({ status: 'ok' })],
    ['get', '/plaintext', async () => 'Hello, World!'],
    ['get', '/json', async () => ({ message: 'Hello, World!' })],
    ['get', '/json-large', async () => fixture],
    ['get', '/compute', async () => compute()],
    ['get', '/users', async (r, s) => {
      const raw = r.query.limit === undefined ? '100' : r.query.limit;
      const l = Number(raw);
      if (!/^\d+$/.test(String(raw)) || l < 1 || l > 100) return s(400, invalid());
      return (await pool.query('SELECT id,name,email FROM users ORDER BY id LIMIT $1', [l])).rows;
    }],
    ['get', '/users/:id', async (r, s) => {
      if (!validId(r.params.id)) return s(400, invalid());
      const row = (await pool.query('SELECT id,name,email FROM users WHERE id=$1', [r.params.id])).rows[0];
      return row || s(404, { error: 'not found' });
    }],
    ['post', '/users', async (r, s) => {
      const { name, email } = r.body || {};
      if (typeof name !== 'string' || !name || [...name].length > 100 || typeof email !== 'string' || !email || [...email].length > 255) return s(400, invalid());
      const row = (await pool.query('INSERT INTO users(name,email) VALUES($1,$2) RETURNING id,name,email', [name, email])).rows[0];
      return s(201, row);
    }],
    ['put', '/users/:id', async (r, s) => {
      const name = r.body?.name;
      if (!validId(r.params.id) || typeof name !== 'string' || !name || [...name].length > 100) return s(400, invalid());
      const row = (await pool.query('UPDATE users SET name=$1 WHERE id=$2 RETURNING id,name,email', [name, r.params.id])).rows[0];
      return row || s(404, { error: 'not found' });
    }],
  ];
  if (process.env.FRAMEWORK === 'express') {
    const express = require('express');
    const app = express(); app.disable('x-powered-by'); app.disable('etag'); app.use(express.json());
    for (const [method, path, fn] of routes) app[method](path, async (req, res, next) => {
      try { const out = await fn(req, (code, body) => { res.status(code); return body; });
        if (typeof out === 'string') res.type('text/plain').send(out); else res.json(out);
      } catch (e) { next(e); }
    });
    app.use((e, req, res, next) => res.status(e.code === '23505' ? 409 : e.type === 'entity.parse.failed' ? 400 : 500).json({ error: e.code === '23505' ? 'conflict' : e.type === 'entity.parse.failed' ? 'invalid request' : 'internal error' }));
    app.listen(8080, '0.0.0.0');
  } else {
    const app = require('fastify')({ logger: false });
    for (const [method, path, fn] of routes) app[method](path, async (req, reply) => {
      const out = await fn(req, (code, body) => { reply.code(code); return body; });
      if (typeof out === 'string') reply.type('text/plain'); return out;
    });
    app.setErrorHandler((e, req, reply) => reply.code(e.code === '23505' ? 409 : e.statusCode === 400 ? 400 : 500).send({ error: e.code === '23505' ? 'conflict' : e.statusCode === 400 ? 'invalid request' : 'internal error' }));
    await app.listen({ port: 8080, host: '0.0.0.0' });
  }
}
