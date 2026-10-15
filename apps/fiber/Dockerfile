FROM golang:1.24.1-bookworm AS build
WORKDIR /src
COPY apps/fiber/ ./
RUN go mod download && CGO_ENABLED=0 go build -mod=readonly -o /server .
FROM debian:bookworm-20250317-slim
COPY --from=build /server /server
EXPOSE 8080
CMD ["/server"]
