.PHONY: check ci

check:
	uvx invoke format
	uvx invoke check
	uvx invoke test

ci:
	uvx invoke ci
