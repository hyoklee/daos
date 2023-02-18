# scons --build-deps=yes install
all:
	which pip3
	pip3 install -r requirements.txt
	mkdir /tmp/install
	spack view --verbose symlink -i  /tmp/install
	ls /tmp/install
	scons --build-deps=yes install ALT_PREFIX=/tmp/install
