package benchmark;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.converter.HttpMessageNotReadableException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

@SpringBootApplication
@RestController
public class Application {
    private final JdbcTemplate db;
    private final Object fixture;
    public Application(JdbcTemplate db, ObjectMapper json) throws Exception {
        this.db = db;
        fixture = json.readValue(Files.readString(Path.of("/fixtures/large.json")), Object.class);
    }
    public static void main(String[] args) { SpringApplication.run(Application.class, args); }
    record User(long id, String name, String email) {}
    private static ResponseEntity<?> error(int status, String text) { return ResponseEntity.status(status).body(Map.of("error", text)); }
    private static boolean validText(Object s, int max) { return s instanceof String && !((String)s).isEmpty() && ((String)s).codePointCount(0, ((String)s).length()) <= max; }
    private static boolean validId(String s) { try { return s.matches("[0-9]+") && Long.parseLong(s) > 0 && Long.parseLong(s) <= 9007199254740991L; } catch (Exception e) { return false; } }
    private List<User> query(String sql, Object... args) { return db.query(sql, (rs, n) -> new User(rs.getLong("id"),rs.getString("name"),rs.getString("email")), args); }
    @GetMapping("/health") Object health() { return Map.of("status", "ok"); }
    @GetMapping(value="/plaintext", produces="text/plain") String plaintext() { return "Hello, World!"; }
    @GetMapping("/json") Object json() { return Map.of("message", "Hello, World!"); }
    @GetMapping("/json-large") Object large() { return fixture; }
    @GetMapping("/compute") Object compute() {
        long a=0;
        for(long i=1;i<=100000;i++) a=(a+((i*i)%97))%1000000007;
        return Map.of("result",a);
    }
    @GetMapping("/users") Object list(@RequestParam(defaultValue="100") String limit) {
        if(!validId(limit) || Long.parseLong(limit)>100) return error(400,"invalid request");
        return query("SELECT id,name,email FROM users ORDER BY id LIMIT ?",Integer.parseInt(limit));
    }
    @GetMapping("/users/{id}") Object get(@PathVariable String id) {
        if(!validId(id)) return error(400,"invalid request");
        var rows=query("SELECT id,name,email FROM users WHERE id=?",Long.parseLong(id));
        return rows.isEmpty() ? error(404,"not found") : rows.getFirst();
    }
    @PostMapping("/users") Object create(@RequestBody Map<String,Object> input) {
        if(!validText(input.get("name"),100) || !validText(input.get("email"),255)) return error(400,"invalid request");
        var row=query("INSERT INTO users(name,email) VALUES(?,?) RETURNING id,name,email",input.get("name"),input.get("email")).getFirst();
        return ResponseEntity.status(201).body(row);
    }
    @PutMapping("/users/{id}") Object update(@PathVariable String id, @RequestBody Map<String,Object> input) {
        if(!validId(id) || !validText(input.get("name"),100)) return error(400,"invalid request");
        var rows=query("UPDATE users SET name=? WHERE id=? RETURNING id,name,email",input.get("name"),Long.parseLong(id));
        return rows.isEmpty() ? error(404,"not found") : rows.getFirst();
    }
    @ExceptionHandler(DuplicateKeyException.class) Object duplicate() { return error(409,"conflict"); }
    @ExceptionHandler(HttpMessageNotReadableException.class) Object malformed() { return error(400,"invalid request"); }
}
