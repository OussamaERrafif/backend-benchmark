FROM composer:2.8.6 AS composer
FROM dunglas/frankenphp:1.4.4-php8.3-bookworm@sha256:285e677100605bd7ea7d2dbea36a4443bf4d4f0caf33e706252dabebea1a1c53
COPY --from=composer /usr/bin/composer /usr/local/bin/composer
RUN install-php-extensions pdo_pgsql mbstring dom opcache \
    && cp "$PHP_INI_DIR/php.ini-production" "$PHP_INI_DIR/php.ini" \
    && apt-get update && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY apps/laravel/ ./
COPY fixtures/large.json /tmp/large.json
RUN php -r 'file_put_contents("/app/fixture.php", "<?php return ".var_export(json_decode(file_get_contents("/tmp/large.json"),true),true).";");'
RUN mkdir -p storage/logs storage/framework/cache storage/framework/sessions storage/framework/views bootstrap/cache app \
    && composer install --no-dev --no-interaction --prefer-dist --optimize-autoloader \
    && chown -R www-data:www-data storage bootstrap/cache \
    && printf 'opcache.enable=1\nopcache.validate_timestamps=0\nexpose_php=Off\n' > /usr/local/etc/php/conf.d/benchmark.ini
COPY apps/laravel/Caddyfile /etc/frankenphp/Caddyfile
ENV APP_ENV=production APP_DEBUG=false GOMAXPROCS=1
EXPOSE 8080
CMD ["frankenphp","run","--config","/etc/frankenphp/Caddyfile","--adapter","caddyfile"]
