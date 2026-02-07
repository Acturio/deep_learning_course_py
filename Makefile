IMAGE_NAME=dlhub-rstudio
CONTAINER_NAME=dlhub-book

# COLORS
GREEN  := $(shell tput -Txterm setaf 2)
YELLOW := $(shell tput -Txterm setaf 3)
WHITE  := $(shell tput -Txterm setaf 7)
RESET  := $(shell tput -Txterm sgr0)

## Show help with `make help`
help:
	@echo ''
	@echo 'Usage:'
	@echo '  ${YELLOW}make${RESET} ${GREEN}<target>${RESET}'
	@echo ''
	@echo 'Targets:'
	@awk '/^[a-zA-Z\-\_0-9]+:/ { \
		helpMessage = match(lastLine, /^## (.*)/); \
		if (helpMessage) { \
			helpCommand = substr($$1, 0, index($$1, ":")-1); \
			helpMessage = substr(lastLine, RSTART + 3, RLENGTH); \
			printf "  ${YELLOW}%-$(TARGET_MAX_CHAR_NUM)s${RESET} ${GREEN}%s${RESET}\n", helpCommand, helpMessage; \
		} \
	} \
	{ lastLine = $$0 }' $(MAKEFILE_LIST)


## Builing the image
build:
	docker build -t $(IMAGE_NAME) .

## Execute a rstudio server session
start:
	docker run -d -p 8787:8787 \
		-e PASSWORD=dlhub \
		-v $$(pwd):/home/rstudio/project \
		-e ROOT=TRUE \
		$(IMAGE_NAME)
	
## Enter to the container (bash)
bash:
	docker exec -it $(CONTAINER_NAME) bash

## Compile the whole book (without entering to the container)
book:
	docker exec -it $(CONTAINER_NAME) cd /home/rstudio/project Rscript -e "bookdown::render_book('index.Rmd','bookdown::gitbook')"

## Stoping the container
stop:
	@# Use a backslash to keep the command in a single shell execution
	CONTAINER_ID=$$(docker ps -q --filter "ancestor=dlhub-rstudio"); \
	if [ -n "$$CONTAINER_ID" ]; then \
		echo "Stopping container: $$CONTAINER_ID"; \
		docker stop $$CONTAINER_ID; \
	else \
		echo "No running container found for dlhub-rstudio"; \
	fi

## Deleting thecontainer
clean:
	docker rm -f $(CONTAINER_NAME) || true
