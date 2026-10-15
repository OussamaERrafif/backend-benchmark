<?php
return ['default'=>'pgsql', 'connections'=>['pgsql'=>[
    'driver'=>'pgsql', 'host'=>'postgres', 'port'=>'5432',
    'database'=>env('POSTGRES_DB','benchmark'), 'username'=>env('POSTGRES_USER','benchmark'), 'password'=>env('POSTGRES_PASSWORD','benchmark'),
    'charset'=>'utf8', 'prefix'=>'', 'search_path'=>'public', 'sslmode'=>'prefer',
    'options'=>[PDO::ATTR_PERSISTENT=>true]
]]];
