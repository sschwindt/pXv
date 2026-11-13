# Installation

From a terminal, clone the repository and open its directory.

```sh
git clone https://github.com/sschwindt/pXv.git
cd pXv
```

## Browser app

Install Node.js 24 or later and use Chrome or Edge with WebCodecs. The app
has no npm dependencies. From the repository checkout, start the local
server and open [pXv](http://127.0.0.1:8040/).

```sh
node web/server.mjs
```

## Python package

Install the core pipeline with Python 3.10 or later.

```sh
python -m pip install -e .
```

To add GIS exports, the Qt interface, and parameter search, install the
corresponding extras.

```sh
python -m pip install -e ".[geo,gui,opt]"
```

You can also create the environment from `environment.yml` with Mamba.

```sh
mamba env create -f environment.yml
mamba activate pxv-env
python -m pip install -e .
```
