# Minimal Akeneo PIM Community Edition image for the AI Retail OS demo.
#
# Akeneo doesn't ship an all-in-one Docker Hub image — the upstream-
# recommended path for CE is to clone `akeneo/pim-community-standard`
# and run their docker-compose. This Dockerfile inverts that: clones
# the repo + composer-installs at build time so the operator only
# touches `make akeneo-*`.
#
# Pinned to v7.0 (latest Community Edition major as of writing).
# Override with --build-arg AKENEO_REF=… to track a fork or tag.

FROM php:8.1-apache

ARG AKENEO_REPO=https://github.com/akeneo/pim-community-standard.git
ARG AKENEO_REF=7.0
ARG AKENEO_DIR=/srv/pim

# PHP extensions Akeneo CE 7.x requires. List from upstream docs.
# bcmath, gd, intl, pcntl, pdo_mysql, opcache, sockets, sodium, xsl, zip.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git unzip curl ca-certificates \
        libicu-dev libxml2-dev libxslt1-dev libzip-dev libpng-dev \
        libfreetype6-dev libjpeg-dev libsodium-dev \
        default-mysql-client \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j"$(nproc)" \
        bcmath gd intl pcntl pdo_mysql opcache sockets sodium xsl zip \
    && pecl install apcu \
    && docker-php-ext-enable apcu \
    && rm -rf /var/lib/apt/lists/*

# Composer
COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

# Apache config — Akeneo serves under /
RUN a2enmod rewrite && \
    sed -i 's|DocumentRoot /var/www/html|DocumentRoot /srv/pim/public|' \
      /etc/apache2/sites-available/000-default.conf && \
    printf '<Directory /srv/pim/public>\n  AllowOverride All\n  Require all granted\n</Directory>\n' \
      >> /etc/apache2/sites-available/000-default.conf

WORKDIR ${AKENEO_DIR}

RUN git clone --depth 1 --branch "${AKENEO_REF}" \
        "${AKENEO_REPO}" "${AKENEO_DIR}" \
    && rm -rf .git \
    && composer install --no-interaction --no-progress --no-scripts --prefer-dist \
    && composer clear-cache

# Pre-create writable dirs Akeneo needs
RUN mkdir -p var/cache var/log var/file_storage public/media \
    && chown -R www-data:www-data var public/media

EXPOSE 80

# Apache foreground — bin/console migrate runs in bootstrap.sh against
# this container, not at image build time, because it needs MySQL up.
CMD ["apache2-foreground"]
