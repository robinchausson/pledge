FROM eclipse-mosquitto:latest

USER root
RUN apk add --no-cache openssl

COPY entrypoint.sh /entrypoint.sh
RUN sed -i -e 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 1883 8883

ENTRYPOINT ["/entrypoint.sh"]

