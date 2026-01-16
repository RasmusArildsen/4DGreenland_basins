SHELL := /bin/sh

.PHONY: up clean

# Build and start the stack
up:
	@NB_UID=$$(id -u) NB_GID=$$(id -g) docker compose up --build

# Remove containers + local image (keeps ./data)
clean:
	docker compose down --rmi local
