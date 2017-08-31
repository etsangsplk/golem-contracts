.PHONY: compile test channels

compile:
	populus compile

test: compile
	pytest

channels: compile
	pytest tests/test_brass_channels.py