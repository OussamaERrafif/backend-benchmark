<?php
use Illuminate\Support\Facades\Route;
use Illuminate\Support\Facades\DB;
use Illuminate\Http\Request;

function invalid() { return response()->json(['error'=>'invalid request'],400); }
function validText($s,$max) { return is_string($s) && mb_strlen($s)>0 && mb_strlen($s)<=$max; }
function validId($s) { return ctype_digit((string)$s) && (int)$s>0 && (int)$s<=9007199254740991; }
function userResponse($row,$status=200) { return $row ? response()->json(['id'=>(int)$row->id,'name'=>$row->name,'email'=>$row->email],$status) : response()->json(['error'=>'not found'],404); }
Route::get('/health',fn()=>['status'=>'ok']);
Route::get('/plaintext',fn()=>response('Hello, World!',200)->header('Content-Type','text/plain'));
Route::get('/json',fn()=>['message'=>'Hello, World!']);
Route::get('/json-large',fn()=>response()->json(require __DIR__.'/../fixture.php'));
Route::get('/compute',function(){ $a=0; for($i=1;$i<=100000;$i++) $a=($a+(($i*$i)%97))%1000000007; return ['result'=>$a]; });
Route::get('/users',function(Request $r){
    $limit=$r->query('limit','100');if(!validId($limit)||(int)$limit>100)return invalid();
    return response()->json(DB::table('users')->select('id','name','email')->orderBy('id')->limit((int)$limit)->get()->map(fn($row)=>['id'=>(int)$row->id,'name'=>$row->name,'email'=>$row->email]));
});
Route::get('/users/{id}',function($id){ if(!validId($id))return invalid();return userResponse(DB::table('users')->select('id','name','email')->where('id',(int)$id)->first()); });
Route::post('/users',function(Request $r){
    $name=$r->input('name');$email=$r->input('email'); if(!validText($name,100)||!validText($email,255))return invalid();
    $row=DB::selectOne('INSERT INTO users(name,email) VALUES(?,?) RETURNING id,name,email',[$name,$email]);return userResponse($row,201);
});
Route::put('/users/{id}',function(Request $r,$id){
    $name=$r->input('name');if(!validId($id)||!validText($name,100))return invalid();
    return userResponse(DB::selectOne('UPDATE users SET name=? WHERE id=? RETURNING id,name,email',[$name,(int)$id]));
});
