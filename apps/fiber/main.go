package main

import (
    "context"
    "encoding/json"
    "errors"
    "os"
    "strconv"
    "unicode/utf8"
    "github.com/gofiber/fiber/v2"
    "github.com/jackc/pgx/v5"
    "github.com/jackc/pgx/v5/pgconn"
    "github.com/jackc/pgx/v5/pgxpool"
)
type User struct { ID int64 `json:"id"`; Name string `json:"name"`; Email string `json:"email"` }
func main() {
    config,err:=pgxpool.ParseConfig(os.Getenv("DATABASE_URL")); if err!=nil {panic(err)}
    size,_:=strconv.Atoi(os.Getenv("DB_POOL_SIZE")); if size==0 {size=20}; config.MaxConns=int32(size); config.MinConns=0
    pool,err:=pgxpool.NewWithConfig(context.Background(),config); if err!=nil {panic(err)}; defer pool.Close()
    content,err:=os.ReadFile("/fixtures/large.json"); if err!=nil {panic(err)}
    var fixture any; if err=json.Unmarshal(content,&fixture);err!=nil {panic(err)}
    invalid:=func(c *fiber.Ctx)error {return c.Status(400).JSON(fiber.Map{"error":"invalid request"})}
    validText:=func(s string,max int)bool {return len(s)>0 && utf8.RuneCountInString(s)<=max}
    id:=func(c *fiber.Ctx)(int64,bool) { s:=c.Params("id"); for _,r:=range s {if r<'0'||r>'9' {return 0,false}}; n,e:=strconv.ParseInt(s,10,64);return n,e==nil&&n>0&&n<=9007199254740991 }
    app:=fiber.New(fiber.Config{DisableStartupMessage:true, ErrorHandler:func(c *fiber.Ctx,e error)error {
        var pg *pgconn.PgError
        if errors.As(e,&pg)&&pg.Code=="23505" {return c.Status(409).JSON(fiber.Map{"error":"conflict"})}
        return c.Status(500).JSON(fiber.Map{"error":"internal error"})
    }})
    app.Get("/health",func(c *fiber.Ctx)error{return c.JSON(fiber.Map{"status":"ok"})})
    app.Get("/plaintext",func(c *fiber.Ctx)error{c.Type("txt");return c.SendString("Hello, World!")})
    app.Get("/json",func(c *fiber.Ctx)error{return c.JSON(fiber.Map{"message":"Hello, World!"})})
    app.Get("/json-large",func(c *fiber.Ctx)error{return c.JSON(fixture)})
    app.Get("/compute",func(c *fiber.Ctx)error{var a int64;for i:=int64(1);i<=100000;i++ {a=(a+((i*i)%97))%1000000007};return c.JSON(fiber.Map{"result":a})})
    app.Get("/users",func(c *fiber.Ctx)error{
        s:=c.Query("limit","100");for _,r:=range s {if r<'0'||r>'9' {return invalid(c)}}
        limit,e:=strconv.Atoi(s);if e!=nil||limit<1||limit>100 {return invalid(c)}
        rows,e:=pool.Query(c.Context(),"SELECT id,name,email FROM users ORDER BY id LIMIT $1",limit);if e!=nil{return e};defer rows.Close()
        out:=[]User{};for rows.Next(){var u User;if e=rows.Scan(&u.ID,&u.Name,&u.Email);e!=nil{return e};out=append(out,u)}
        if e=rows.Err();e!=nil{return e};return c.JSON(out)
    })
    app.Get("/users/:id",func(c *fiber.Ctx)error{
        n,ok:=id(c);if !ok{return invalid(c)};var u User
        e:=pool.QueryRow(c.Context(),"SELECT id,name,email FROM users WHERE id=$1",n).Scan(&u.ID,&u.Name,&u.Email)
        if errors.Is(e,pgx.ErrNoRows){return c.Status(404).JSON(fiber.Map{"error":"not found"})};if e!=nil{return e};return c.JSON(u)
    })
    app.Post("/users",func(c *fiber.Ctx)error{
        var data struct{Name string `json:"name"`;Email string `json:"email"`}
        if json.Unmarshal(c.Body(),&data)!=nil||!validText(data.Name,100)||!validText(data.Email,255){return invalid(c)}
        var u User;e:=pool.QueryRow(c.Context(),"INSERT INTO users(name,email) VALUES($1,$2) RETURNING id,name,email",data.Name,data.Email).Scan(&u.ID,&u.Name,&u.Email)
        if e!=nil{return e};return c.Status(201).JSON(u)
    })
    app.Put("/users/:id",func(c *fiber.Ctx)error{
        n,ok:=id(c);var data struct{Name string `json:"name"`}
        if !ok||json.Unmarshal(c.Body(),&data)!=nil||!validText(data.Name,100){return invalid(c)}
        var u User;e:=pool.QueryRow(c.Context(),"UPDATE users SET name=$1 WHERE id=$2 RETURNING id,name,email",data.Name,n).Scan(&u.ID,&u.Name,&u.Email)
        if errors.Is(e,pgx.ErrNoRows){return c.Status(404).JSON(fiber.Map{"error":"not found"})};if e!=nil{return e};return c.JSON(u)
    })
    if err=app.Listen(":8080");err!=nil {panic(err)}
}
