FROM rust:1.85.1-bookworm AS build
WORKDIR /src
COPY apps/axum/ ./
RUN cargo build --release --locked
FROM debian:bookworm-20250317-slim
COPY --from=build /src/target/release/benchmark /server
EXPOSE 8080
CMD ["/server"]
