FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go build -o /dafter-control ./cmd/dafter-control

FROM alpine:latest
COPY --from=build /dafter-control /usr/local/bin/
ENTRYPOINT ["dafter-control"]
