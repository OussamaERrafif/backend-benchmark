FROM mcr.microsoft.com/dotnet/sdk:8.0.407 AS build
WORKDIR /src
COPY apps/aspnet/ ./
RUN dotnet restore --locked-mode && dotnet publish -c Release --no-restore -o /out
FROM mcr.microsoft.com/dotnet/aspnet:8.0.14
WORKDIR /app
COPY --from=build /out ./
ENV ASPNETCORE_ENVIRONMENT=Production DOTNET_EnableDiagnostics=0
EXPOSE 8080
CMD ["dotnet","Benchmark.dll"]
