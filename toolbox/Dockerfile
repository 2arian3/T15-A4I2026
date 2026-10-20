FROM us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:1.14.0
COPY tools.yaml /app/tools.yaml
ENTRYPOINT ["/toolbox"]
CMD ["--config=/app/tools.yaml", "--address=0.0.0.0", "--port=8080"]
