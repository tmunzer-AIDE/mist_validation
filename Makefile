DOCKER_IMAGE ?= mist-validation
FRONTEND_DIR  = frontend
BACKEND_DIR   = backend
STATIC_DIR    = $(BACKEND_DIR)/app/frontend/static
INDEX_DIR     = $(BACKEND_DIR)/app/frontend

.PHONY: angular clean docker all

# Build Angular frontend and copy output into the backend static directory
angular:
	cd $(FRONTEND_DIR) && ng build --deploy-url static/
	mkdir -p $(STATIC_DIR)
	rm -rf $(STATIC_DIR)/*
	cp $(FRONTEND_DIR)/dist/browser/browser/* $(STATIC_DIR)/
	cp $(FRONTEND_DIR)/dist/browser/browser/index.html $(INDEX_DIR)/

# Build the Docker image (runs angular first)
docker: angular
	docker buildx build --platform linux/amd64 -t tmunzer/mist-validation .

# Shorthand: build everything
all: docker

# Remove Angular build artifacts and copied static files
clean:
	rm -rf $(FRONTEND_DIR)/dist
	rm -rf $(STATIC_DIR)/*
