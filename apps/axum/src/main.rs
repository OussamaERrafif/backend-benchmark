use axum::{extract::{State, Path, Query, rejection::JsonRejection}, http::StatusCode, response::{IntoResponse, Response}, routing::{get, post}, Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::{postgres::PgPoolOptions, PgPool};
use std::{collections::HashMap, env, sync::Arc};

struct App { pool: PgPool, fixture: Value }
type Shared = Arc<App>;
#[derive(Serialize, sqlx::FromRow)]
struct User { id: i64, name: String, email: String }
#[derive(Deserialize)]
struct Input { name: String, email: Option<String> }
fn error(code: StatusCode, text: &str) -> Response { (code, Json(json!({"error":text}))).into_response() }
fn invalid() -> Response { error(StatusCode::BAD_REQUEST,"invalid request") }
fn valid_text(s: &str, max: usize) -> bool { !s.is_empty() && s.chars().count() <= max }
fn id(s: &str) -> Option<i64> { if !s.bytes().all(|c| c.is_ascii_digit()) {return None} s.parse::<i64>().ok().filter(|n| *n > 0 && *n <= 9007199254740991) }
fn db_error(e: sqlx::Error) -> Response {
    if e.as_database_error().and_then(|e|e.code()).as_deref() == Some("23505") { error(StatusCode::CONFLICT,"conflict") }
    else { error(StatusCode::INTERNAL_SERVER_ERROR,"internal error") }
}
async fn list(State(app): State<Shared>, Query(q): Query<HashMap<String,String>>) -> Response {
    let limit = match id(q.get("limit").map(String::as_str).unwrap_or("100")) { Some(n) if n<=100 => n as i32, _=>return invalid() };
    match sqlx::query_as::<_,User>("SELECT id,name,email FROM users ORDER BY id LIMIT $1").bind(limit).fetch_all(&app.pool).await {
        Ok(rows)=>Json(rows).into_response(),Err(e)=>db_error(e)
    }
}
async fn read(State(app): State<Shared>, Path(s): Path<String>) -> Response {
    let n=match id(&s) {Some(n)=>n,None=>return invalid()};
    match sqlx::query_as::<_,User>("SELECT id,name,email FROM users WHERE id=$1").bind(n).fetch_optional(&app.pool).await {
        Ok(Some(row))=>Json(row).into_response(),Ok(None)=>error(StatusCode::NOT_FOUND,"not found"),Err(e)=>db_error(e)
    }
}
async fn create(State(app): State<Shared>, body: Result<Json<Input>, JsonRejection>) -> Response {
    let data=match body {Ok(Json(data))=>data,Err(_)=>return invalid()};
    let email=match data.email {Some(s) if valid_text(&s,255)=>s,_=>return invalid()};
    if !valid_text(&data.name,100) {return invalid()}
    match sqlx::query_as::<_,User>("INSERT INTO users(name,email) VALUES($1,$2) RETURNING id,name,email").bind(data.name).bind(email).fetch_one(&app.pool).await {
        Ok(row)=>(StatusCode::CREATED,Json(row)).into_response(),Err(e)=>db_error(e)
    }
}
async fn update(State(app): State<Shared>, Path(s): Path<String>, body: Result<Json<Input>, JsonRejection>) -> Response {
    let n=match id(&s) {Some(n)=>n,None=>return invalid()};
    let data=match body {Ok(Json(data)) if valid_text(&data.name,100)=>data,_=>return invalid()};
    match sqlx::query_as::<_,User>("UPDATE users SET name=$1 WHERE id=$2 RETURNING id,name,email").bind(data.name).bind(n).fetch_optional(&app.pool).await {
        Ok(Some(row))=>Json(row).into_response(),Ok(None)=>error(StatusCode::NOT_FOUND,"not found"),Err(e)=>db_error(e)
    }
}
#[tokio::main]
async fn main() {
    let pool=PgPoolOptions::new().min_connections(0).max_connections(env::var("DB_POOL_SIZE").unwrap_or("20".into()).parse().unwrap()).connect(&env::var("DATABASE_URL").unwrap()).await.unwrap();
    let fixture=serde_json::from_str(&std::fs::read_to_string("/fixtures/large.json").unwrap()).unwrap();
    let app=Router::new()
        .route("/health",get(||async {Json(json!({"status":"ok"}))}))
        .route("/plaintext",get(||async {"Hello, World!"}))
        .route("/json",get(||async {Json(json!({"message":"Hello, World!"}))}))
        .route("/json-large",get(|State(app): State<Shared>|async move {Json(app.fixture.clone())}))
        .route("/compute",get(||async {let mut a:i64=0;for i in 1i64..=100000 {a=(a+((i*i)%97))%1000000007;}Json(json!({"result":a}))}))
        .route("/users",get(list).post(create))
        .route("/users/{id}",get(read).put(update))
        .with_state(Arc::new(App{pool,fixture}));
    let listener=tokio::net::TcpListener::bind("0.0.0.0:8080").await.unwrap();
    axum::serve(listener,app).await.unwrap();
}
