# Postgres + pgvector. The wrapper generates the database password on first
# boot into the shared `secrets` volume (no .env needed).
FROM pgvector/pgvector:0.8.6-pg17-bookworm
COPY lecta-entrypoint.sh /usr/local/bin/lecta-entrypoint.sh
COPY init-extensions.sql /docker-entrypoint-initdb.d/10-extensions.sql
RUN chmod 0755 /usr/local/bin/lecta-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/lecta-entrypoint.sh"]
CMD ["postgres"]
