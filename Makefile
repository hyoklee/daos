all:
	pip install -r requirements.txt
	scons --build-deps=yes install
