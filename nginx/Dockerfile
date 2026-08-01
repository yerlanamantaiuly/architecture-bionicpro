FROM nginx:1.25-alpine

# Official nginx image has no cache_purge module; invalidation uses versioned S3 keys.
# Keep a writable cache directory.
RUN mkdir -p /var/cache/nginx/reports \
    && chown -R nginx:nginx /var/cache/nginx

COPY cdn.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
