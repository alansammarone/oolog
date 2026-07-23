.PHONY: check

check:
	uvx invoke format
	uvx invoke check
	uvx invoke test
