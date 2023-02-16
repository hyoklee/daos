all:
	which pip3
	pip3 install -r requirements.txt
	scons --build-deps=yes install
