import json
import os
from contextlib import asynccontextmanager
import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

@asynccontextmanager
async def lifespan(app):
    app.state.pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=0,
        max_size=max(1, int(os.getenv('DB_POOL_SIZE', '20')) // int(os.getenv('APP_WORKERS', '1'))))
    with open('/fixtures/large.json') as f:
        app.state.fixture = json.load(f)
    yield
    await app.state.pool.close()

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

def error(code, message):
    return JSONResponse({'error': message}, status_code=code)

def valid_id(value):
    return value.isascii() and value.isdigit() and 0 < int(value) <= 9007199254740991

async def body(request):
    try:
        value = await request.json()
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeError):
        return {}

def valid_text(value, maximum):
    return isinstance(value, str) and 0 < len(value) <= maximum

@app.exception_handler(asyncpg.UniqueViolationError)
async def conflict(request, exc):
    return error(409, 'conflict')

@app.get('/health')
async def health(): return {'status': 'ok'}

@app.get('/plaintext')
async def plaintext(): return PlainTextResponse('Hello, World!')

@app.get('/json')
async def small_json(): return {'message': 'Hello, World!'}

@app.get('/json-large')
async def large_json(): return app.state.fixture

@app.get('/compute')
async def compute():
    accumulator = 0
    for i in range(1, 100001):
        accumulator = (accumulator + ((i * i) % 97)) % 1000000007
    return {'result': accumulator}

@app.get('/users')
async def users(request: Request):
    limit = request.query_params.get('limit', '100')
    if not limit.isascii() or not limit.isdigit() or not 1 <= int(limit) <= 100:
        return error(400, 'invalid request')
    rows = await app.state.pool.fetch('SELECT id,name,email FROM users ORDER BY id LIMIT $1', int(limit))
    return [dict(row) for row in rows]

@app.get('/users/{id}')
async def user(id: str):
    if not valid_id(id): return error(400, 'invalid request')
    row = await app.state.pool.fetchrow('SELECT id,name,email FROM users WHERE id=$1', int(id))
    return dict(row) if row else error(404, 'not found')

@app.post('/users')
async def create(request: Request):
    data = await body(request)
    if not valid_text(data.get('name'), 100) or not valid_text(data.get('email'), 255):
        return error(400, 'invalid request')
    row = await app.state.pool.fetchrow('INSERT INTO users(name,email) VALUES($1,$2) RETURNING id,name,email', data['name'], data['email'])
    return JSONResponse(dict(row), status_code=201)

@app.put('/users/{id}')
async def update(id: str, request: Request):
    data = await body(request)
    if not valid_id(id) or not valid_text(data.get('name'), 100): return error(400, 'invalid request')
    row = await app.state.pool.fetchrow('UPDATE users SET name=$1 WHERE id=$2 RETURNING id,name,email', data['name'], int(id))
    return dict(row) if row else error(404, 'not found')
