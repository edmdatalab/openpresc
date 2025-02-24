This document describes the local setup process for devs.
It is a work in progress and only has instructions for macOS for now.

## macOS local setup

### Prerequisites
These instructions assumes:
 - Usage of [Homebrew](https://brew.sh/) as a package manager
 - [Postgres.app](https://postgresapp.com/) has been installed

### Install gdal
This can be done [via Homebrew](https://formulae.brew.sh/formula/gdal).

### Set up a virtualenv
Set up a virtual environment with the Python version specified in `.python-version`.
The architecture of your Python executable must match that of gdal's (which should
be x86_64 if installed via Homebrew).
If your Python is of another architecture (e.g. aarch64), you can get the x86_64
Python [via Homebrew](https://formulae.brew.sh/formula/python@3.12).

### Install Python dependencies
Install the Python dependencies into your virtual environment:
```sh
    pip install -r requirements.dev.txt
    pip install -r requirements.txt
```
### Create environment file
Copy `environment-sample` to `environment`, leaving the fields unchanged for now.

### Set up a Postgres database
Decide on a name for the Postgres database (e.g. `openp`) and edit the `DB_NAME`
field in `environment` appropriately.

Run `psql` and create the database. For `openp`, this would be:
```sql
    CREATE DATABASE openp;
```
Then, add PostGIS extensions:
```sql
    \c openp;
    CREATE EXTENSION postgis;
```

This should allow you to run the database migrations:
```sh
    cd openprescribing
    python manage.py migrate
```

### Install JavaScript dependencies
Make sure you have the latest version of nodejs, then:
```sh
    cd openprescribing/media/js
    npm install -g browserify
    npm install -g jshint
    npm install -g less
    npm install
```

### Build the front-end assets
Build the front-end assets:
```sh
    npm run build
```
This should allow you to see the front-end assets when you
run the local server.

### Copy data from the production server
Log onto the production server, and use
[`manage.py dumpdata`](https://docs.djangoproject.com/en/5.1/ref/django-admin/#dumpdata)
to dump the following models into a JSON file:
```python
	frontend.Pratice 
	frontend.PCT
	frontend.PCN 
	frontend.STP
	frontend.RegionalTeam
	frontend.Presentation
	frontend.Chemical
	frontend.Product
	frontend.Section
```
Transfer the JSON file to your local machine, then use
[`manage.py loaddata`](https://docs.djangoproject.com/en/5.1/ref/django-admin/#loaddata)
to load the data into the Postgres database.

Also copy the prescribing SQLite database onto the local machine. On the production
server, the database it at `/mnt/database/matrixstore/matrixstore_live.sqlite`, and
on your local machine, it should be at
`openprescribing/pipeline/data/matrixstore_build/matrixstore_live.sqlite`.

At this point, you should be able to run the local server and verify that the
"Analyse" and "Find a Practice" pages work.
