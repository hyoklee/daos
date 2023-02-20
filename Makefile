# scons --build-deps=yes install
all:
	which pip3
	pip3 install -r requirements.txt
	mkdir /tmp/install
	spack view --verbose symlink -i  /tmp/install argobots cmocka libfuse hwloc go isa-l isa-l_crypto libfabric libuuid libunwind libyaml mercury+boostsys mpich openssl pmdk protobuf-c py-distro readline spdk scons
	ls /tmp/install
	scons --build-deps=yes install ALT_PREFIX=/tmp/install
