
# Setup test environment
```
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

# Run tests

First activate the virtual environment:
```
source ./venv/bin/activate
```

Then run:
```
python -m unittest --verbose
```

To execute only one test, suffix with the fully qualified test name. Example:
```
python -m unittest tests_database_migration.TestsDatabaseMigration.test_get_iocs_should_return_200_after_update_from_v2_4_22
```

# Test creation

First make sure the database volume is empty:
```
docker volume rm iris-web_db_data
```

Then checkout and start the iris version you want to start from:
```
git checkout vM.m.p
docker compose up
```

If necessary for your test, use Iris to set the database in a particular state. When this is done, dump the database:
```
docker exec iriswebapp_db pg_dump -U postgres iris_db | gzip > database_dumps/vM.m.p_<DESCRIPTION>.gz
```
