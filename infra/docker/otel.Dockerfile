FROM otel/opentelemetry-collector-contrib:0.123.0 AS collector

FROM alpine:3.21

RUN apk add --no-cache ca-certificates curl
COPY --from=collector /otelcol-contrib /otelcol-contrib

USER 10001:10001
ENTRYPOINT ["/otelcol-contrib"]
