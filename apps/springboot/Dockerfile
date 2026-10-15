FROM maven:3.9.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY apps/springboot/pom.xml ./
RUN --mount=type=cache,target=/root/.m2 mvn -B dependency:go-offline
COPY apps/springboot/src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B package -DskipTests
FROM eclipse-temurin:21.0.6_7-jre-jammy
WORKDIR /app
COPY --from=build /src/target/benchmark-1.0.0.jar app.jar
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=70"
EXPOSE 8080
CMD ["java","-jar","app.jar"]
