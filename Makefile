# scons --build-deps=yes install
all:
	which pip3
	pip3 install -r requirements.txt
	scons install
