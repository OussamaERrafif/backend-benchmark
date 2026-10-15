using Npgsql;
using System.Text.Json;

var builder = WebApplication.CreateBuilder(args);
builder.Logging.ClearProviders();
var cs = new NpgsqlConnectionStringBuilder {
    Host = "postgres", Database = Environment.GetEnvironmentVariable("POSTGRES_DB") ?? "benchmark",
    Username = Environment.GetEnvironmentVariable("POSTGRES_USER") ?? "benchmark",
    Password = Environment.GetEnvironmentVariable("POSTGRES_PASSWORD") ?? "benchmark",
    MaxPoolSize = int.Parse(Environment.GetEnvironmentVariable("DB_POOL_SIZE") ?? "20"), MinPoolSize = 0,
    Pooling = true
};
await using var db = NpgsqlDataSource.Create(cs.ConnectionString);
var fixture = JsonSerializer.Deserialize<JsonElement>(File.ReadAllText("/fixtures/large.json"));
var app = builder.Build();
app.Use(async (ctx, next) => {
    try { await next(ctx); }
    catch (PostgresException e) when (e.SqlState == "23505") { ctx.Response.StatusCode=409; await ctx.Response.WriteAsJsonAsync(new {error="conflict"}); }
    catch (JsonException) { ctx.Response.StatusCode=400; await ctx.Response.WriteAsJsonAsync(new {error="invalid request"}); }
});
IResult Error(int code, string text) => Results.Json(new {error=text}, statusCode:code);
bool ValidId(string? s) => s != null && s.All(c => c >= '0' && c <= '9') && long.TryParse(s,out var n) && n > 0 && n <= 9007199254740991;
bool Text(JsonElement data, string key, int max) => data.ValueKind==JsonValueKind.Object && data.TryGetProperty(key,out var v) && v.ValueKind==JsonValueKind.String && v.GetString()!.Length > 0 && v.GetString()!.EnumerateRunes().Count() <= max;
async Task<List<User>> Query(string sql, params object[] values) {
    await using var cmd = db.CreateCommand(sql);
    foreach(var value in values) cmd.Parameters.Add(new NpgsqlParameter {Value=value});
    await using var r = await cmd.ExecuteReaderAsync(); var rows = new List<User>();
    while(await r.ReadAsync()) rows.Add(new User(r.GetInt64(0),r.GetString(1),r.GetString(2)));
    return rows;
}
app.MapGet("/health", () => new {status="ok"});
app.MapGet("/plaintext", () => Results.Text("Hello, World!","text/plain"));
app.MapGet("/json", () => new {message="Hello, World!"});
app.MapGet("/json-large", () => fixture);
app.MapGet("/compute", () => { long a=0; for(long i=1;i<=100000;i++) a=(a+((i*i)%97))%1000000007; return new {result=a}; });
app.MapGet("/users", async (HttpRequest r) => {
    var limit = r.Query["limit"].FirstOrDefault() ?? "100";
    if(!ValidId(limit) || long.Parse(limit)>100) return Error(400,"invalid request");
    return Results.Json(await Query("SELECT id,name,email FROM users ORDER BY id LIMIT $1",int.Parse(limit)));
});
app.MapGet("/users/{id}", async (string id) => {
    if(!ValidId(id)) return Error(400,"invalid request");
    var rows=await Query("SELECT id,name,email FROM users WHERE id=$1",long.Parse(id));
    return rows.Count==0 ? Error(404,"not found") : Results.Json(rows[0]);
});
app.MapPost("/users", async (HttpRequest r) => {
    var data=await JsonSerializer.DeserializeAsync<JsonElement>(r.Body);
    if(!Text(data,"name",100) || !Text(data,"email",255)) return Error(400,"invalid request");
    var rows=await Query("INSERT INTO users(name,email) VALUES($1,$2) RETURNING id,name,email",data.GetProperty("name").GetString()!,data.GetProperty("email").GetString()!);
    return Results.Json(rows[0],statusCode:201);
});
app.MapPut("/users/{id}", async (string id,HttpRequest r) => {
    var data=await JsonSerializer.DeserializeAsync<JsonElement>(r.Body);
    if(!ValidId(id) || !Text(data,"name",100)) return Error(400,"invalid request");
    var rows=await Query("UPDATE users SET name=$1 WHERE id=$2 RETURNING id,name,email",data.GetProperty("name").GetString()!,long.Parse(id));
    return rows.Count==0 ? Error(404,"not found") : Results.Json(rows[0]);
});
await app.RunAsync("http://0.0.0.0:8080");
record User(long Id,string Name,string Email);
