#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import json
import os
from contextlib import closing
from unittest import mock

import pytest
import sqlalchemy

from airflow.models import Connection
from airflow.models.dag import DAG

try:
    import MySQLdb.cursors

    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False

from airflow.providers.mysql.hooks.mysql import MySqlHook
from airflow.utils import timezone

from tests_common.test_utils.asserts import assert_equal_ignore_multiple_spaces

SSL_DICT = {"cert": "/tmp/client-cert.pem", "ca": "/tmp/server-ca.pem", "key": "/tmp/client-key.pem"}
INSERT_SQL_STATEMENT = "INSERT INTO connection (id, conn_id, conn_type, description, host, `schema`, login, password, port, is_encrypted, is_extra_encrypted, extra) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL not available")
class TestMySqlHookConn:
    def setup_method(self):
        self.connection = Connection(
            conn_type="mysql",
            login="login",
            password="password",
            host="host",
            schema="schema",
        )

        self.db_hook = MySqlHook()
        self.db_hook.get_connection = mock.Mock()
        self.db_hook.get_connection.return_value = self.connection

    @mock.patch("MySQLdb.connect")
    def test_get_conn(self, mock_connect):
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["user"] == "login"
        assert kwargs["passwd"] == "password"
        assert kwargs["host"] == "host"
        assert kwargs["db"] == "schema"

    @mock.patch("MySQLdb.connect")
    def test_dummy_connection_setter(self, mock_connect):
        self.db_hook.get_conn()

        self.db_hook.connection = "Won't affect anything"
        assert self.db_hook.connection != "Won't affect anything"

        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["user"] == "login"
        assert kwargs["passwd"] == "password"
        assert kwargs["host"] == "host"
        assert kwargs["db"] == "schema"

    @mock.patch("MySQLdb.connect")
    @pytest.mark.parametrize(
        "connection_params, expected_uri",
        [
            pytest.param(
                {
                    "login": "login",
                    "password": "password",
                    "host": "host",
                    "schema": "schema",
                    "port": None,
                    "extra": json.dumps({"charset": "utf-8"}),
                },
                "mysql://login:password@host/schema?charset=utf-8",
                id="basic_connection_with_charset",
            ),
            pytest.param(
                {
                    "login": "user@domain",
                    "password": "pass/word!",
                    "host": "host",
                    "schema": "schema",
                    "port": None,
                    "extra": json.dumps({"charset": "utf-8"}),
                },
                "mysql://user%40domain:pass%2Fword%21@host/schema?charset=utf-8",
                id="special_chars_in_credentials",
            ),
            pytest.param(
                {
                    "login": "user@domain",
                    "password": "password",
                    "host": "host",
                    "schema": "schema",
                    "port": None,
                    "extra": json.dumps({"client": "mysql-connector-python"}),
                },
                "mysql+mysqlconnector://user%40domain:password@host/schema",
                id="mysql_connector_python",
            ),
            pytest.param(
                {
                    "login": "user@domain",
                    "password": "password",
                    "host": "host",
                    "schema": "schema",
                    "port": 3307,
                    "extra": json.dumps({"client": "mysql-connector-python"}),
                },
                "mysql+mysqlconnector://user%40domain:password@host:3307/schema",
                id="mysql_connector_with_port",
            ),
            pytest.param(
                {
                    "login": "user@domain",
                    "password": "password",
                    "host": "host",
                    "schema": "db/name",
                    "port": 3307,
                    "extra": json.dumps({"client": "mysql-connector-python"}),
                },
                "mysql+mysqlconnector://user%40domain:password@host:3307/db%2Fname",
                id="special_chars_in_schema",
            ),
            pytest.param(
                {
                    "login": "user@domain",
                    "password": "password",
                    "host": "host",
                    "schema": "schema",
                    "port": 3307,
                    "extra": json.dumps(
                        {
                            "client": "mysql-connector-python",
                            "ssl_ca": "/path/to/ca",
                            "ssl_cert": "/path/to/cert with space",
                        }
                    ),
                },
                "mysql+mysqlconnector://user%40domain:password@host:3307/schema?ssl_ca=%2Fpath%2Fto%2Fca&ssl_cert=%2Fpath%2Fto%2Fcert+with+space",
                id="ssl_parameters",
            ),
        ],
    )
    def test_get_uri(self, mock_connect, connection_params, expected_uri):
        """Test get_uri method with various connection parameters."""
        for key, value in connection_params.items():
            setattr(self.connection, key, value)

        assert self.db_hook.get_uri() == expected_uri

    @mock.patch("MySQLdb.connect")
    def test_get_conn_from_connection(self, mock_connect):
        conn = Connection(login="login-conn", password="password-conn", host="host", schema="schema")
        hook = MySqlHook(connection=conn)
        hook.get_conn()
        mock_connect.assert_called_once_with(
            user="login-conn", passwd="password-conn", host="host", db="schema", port=3306
        )

    @mock.patch("MySQLdb.connect")
    def test_get_conn_from_connection_with_schema(self, mock_connect):
        conn = Connection(login="login-conn", password="password-conn", host="host", schema="schema")
        hook = MySqlHook(connection=conn, schema="schema-override")
        hook.get_conn()
        mock_connect.assert_called_once_with(
            user="login-conn", passwd="password-conn", host="host", db="schema-override", port=3306
        )

    @mock.patch("MySQLdb.connect")
    def test_get_conn_port(self, mock_connect):
        self.connection.port = 3307
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["port"] == 3307

    @mock.patch("MySQLdb.connect")
    def test_get_conn_charset(self, mock_connect):
        self.connection.extra = json.dumps({"charset": "utf-8"})
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["charset"] == "utf-8"
        assert kwargs["use_unicode"] is True

    @mock.patch("MySQLdb.connect")
    def test_get_conn_cursor(self, mock_connect):
        self.connection.extra = json.dumps({"cursor": "sscursor"})
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["cursorclass"] == MySQLdb.cursors.SSCursor

    @mock.patch("MySQLdb.connect")
    def test_get_conn_local_infile(self, mock_connect):
        self.db_hook.local_infile = True
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["local_infile"] == 1

    @mock.patch("MySQLdb.connect")
    def test_get_con_unix_socket(self, mock_connect):
        self.connection.extra = json.dumps({"unix_socket": "/tmp/socket"})
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["unix_socket"] == "/tmp/socket"

    @mock.patch("MySQLdb.connect")
    def test_get_conn_ssl_as_dictionary(self, mock_connect):
        self.connection.extra = json.dumps({"ssl": SSL_DICT})
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["ssl"] == SSL_DICT

    @mock.patch("MySQLdb.connect")
    def test_get_conn_ssl_as_string(self, mock_connect):
        self.connection.extra = json.dumps({"ssl": json.dumps(SSL_DICT)})
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["ssl"] == SSL_DICT

    @mock.patch("MySQLdb.connect")
    def test_get_ssl_mode(self, mock_connect):
        self.connection.extra = json.dumps({"ssl_mode": "DISABLED"})
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["ssl_mode"] == "DISABLED"

    @mock.patch("MySQLdb.connect")
    @mock.patch("airflow.providers.amazon.aws.hooks.base_aws.AwsBaseHook.get_client_type")
    def test_get_conn_rds_iam(self, mock_client, mock_connect, monkeypatch):
        monkeypatch.setenv("AIRFLOW_CONN_TEST_AWS_IAM_CONN", '{"conn_type": "aws"}')
        self.connection.extra = '{"iam":true, "aws_conn_id": "test_aws_iam_conn"}'
        mock_client.return_value.generate_db_auth_token.return_value = "aws_token"
        self.db_hook.get_conn()
        mock_connect.assert_called_once_with(
            user="login",
            passwd="aws_token",
            host="host",
            db="schema",
            port=3306,
            read_default_group="enable-cleartext-plugin",
        )

    @mock.patch("MySQLdb.connect")
    def test_get_conn_init_command(self, mock_connect):
        self.db_hook.init_command = "SET time_zone = '+00:00';"
        self.db_hook.get_conn()
        assert mock_connect.call_count == 1
        args, kwargs = mock_connect.call_args
        assert args == ()
        assert kwargs["init_command"] == "SET time_zone = '+00:00';"


class MockMySQLConnectorConnection:
    DEFAULT_AUTOCOMMIT = "default"

    def __init__(self):
        self._autocommit = self.DEFAULT_AUTOCOMMIT

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, autocommit):
        self._autocommit = autocommit


@pytest.mark.db_test
class TestMySqlHook:
    def setup_method(self):
        self.cur = mock.MagicMock(rowcount=0)
        self.conn = mock.MagicMock()
        self.conn.cursor.return_value = self.cur
        conn = self.conn

        class SubMySqlHook(MySqlHook):
            conn_name_attr = "test_conn_id"

            def get_conn(self):
                return conn

        self.db_hook = SubMySqlHook()

    @pytest.mark.parametrize("autocommit", [True, False])
    def test_set_autocommit_mysql_connector(self, autocommit):
        conn = MockMySQLConnectorConnection()
        self.db_hook.set_autocommit(conn, autocommit)
        assert conn.autocommit is autocommit

    def test_get_autocommit_mysql_connector(self):
        conn = MockMySQLConnectorConnection()
        assert self.db_hook.get_autocommit(conn) == MockMySQLConnectorConnection.DEFAULT_AUTOCOMMIT

    def test_set_autocommit_mysqldb(self):
        autocommit = False
        self.db_hook.set_autocommit(self.conn, autocommit)
        self.conn.autocommit.assert_called_once_with(autocommit)

    def test_get_autocommit_mysqldb(self):
        self.db_hook.get_autocommit(self.conn)
        self.conn.get_autocommit.assert_called_once()

    def test_run_without_autocommit(self):
        sql = "SQL"
        self.conn.get_autocommit.return_value = False

        # Default autocommit setting should be False.
        # Testing default autocommit value as well as run() behavior.
        self.db_hook.run(sql, autocommit=False)
        self.conn.autocommit.assert_called_once_with(False)
        self.cur.execute.assert_called_once_with(sql)
        assert self.conn.commit.call_count == 1

    def test_run_with_autocommit(self):
        sql = "SQL"
        self.db_hook.run(sql, autocommit=True)
        self.conn.autocommit.assert_called_once_with(True)
        self.cur.execute.assert_called_once_with(sql)
        self.conn.commit.assert_not_called()

    def test_run_with_parameters(self):
        sql = "SQL"
        parameters = ("param1", "param2")
        self.db_hook.run(sql, autocommit=True, parameters=parameters)
        self.conn.autocommit.assert_called_once_with(True)
        self.cur.execute.assert_called_once_with(sql, parameters)
        self.conn.commit.assert_not_called()

    def test_run_multi_queries(self):
        sql = ["SQL1", "SQL2"]
        self.db_hook.run(sql, autocommit=True)
        self.conn.autocommit.assert_called_once_with(True)
        for i, item in enumerate(self.cur.execute.call_args_list):
            args, kwargs = item
            assert len(args) == 1
            assert args[0] == sql[i]
            assert kwargs == {}
        calls = [mock.call(sql[0]), mock.call(sql[1])]
        self.cur.execute.assert_has_calls(calls, any_order=True)
        self.conn.commit.assert_not_called()

    def test_bulk_load(self):
        self.db_hook.bulk_load("table", "/tmp/file")
        self.cur.execute.assert_called_once_with(
            "LOAD DATA LOCAL INFILE %s INTO TABLE `table`", ("/tmp/file",)
        )

    def test_bulk_dump(self):
        self.db_hook.bulk_dump("table", "/tmp/file")
        self.cur.execute.assert_called_once_with("SELECT * INTO OUTFILE %s FROM `table`", ("/tmp/file",))

    def test_serialize_cell(self):
        assert self.db_hook._serialize_cell("foo", None) == "foo"

    @pytest.mark.parametrize("table", ["table", "where"])
    def test_bulk_load_custom(self, table):
        self.db_hook.bulk_load_custom(
            table,
            "/tmp/file",
            "IGNORE",
            """FIELDS TERMINATED BY ';'
            OPTIONALLY ENCLOSED BY '"'
            IGNORE 1 LINES""",
        )
        self.cur.execute.assert_called_once_with(
            f"LOAD DATA LOCAL INFILE %s %s INTO TABLE `{table}` %s",
            (
                "/tmp/file",
                "IGNORE",
                """FIELDS TERMINATED BY ';'
            OPTIONALLY ENCLOSED BY '"'
            IGNORE 1 LINES""",
            ),
        )

    def test_reserved_words(self):
        hook = MySqlHook()
        assert hook.reserved_words == sqlalchemy.dialects.mysql.reserved_words.RESERVED_WORDS_MYSQL

    def test_generate_insert_sql_without_already_escaped_column_name(self):
        values = [
            "1",
            "mssql_conn",
            "mssql",
            "MSSQL connection",
            "localhost",
            "airflow",
            "admin",
            "admin",
            1433,
            False,
            False,
            {},
        ]
        target_fields = [
            "id",
            "conn_id",
            "conn_type",
            "description",
            "host",
            "schema",
            "login",
            "password",
            "port",
            "is_encrypted",
            "is_extra_encrypted",
            "extra",
        ]
        hook = MySqlHook()
        assert (
            hook._generate_insert_sql(table="connection", values=values, target_fields=target_fields)
            == INSERT_SQL_STATEMENT
        )

    def test_generate_insert_sql_with_already_escaped_column_name(self):
        values = [
            "1",
            "mssql_conn",
            "mssql",
            "MSSQL connection",
            "localhost",
            "airflow",
            "admin",
            "admin",
            1433,
            False,
            False,
            {},
        ]
        target_fields = [
            "id",
            "conn_id",
            "conn_type",
            "description",
            "host",
            "`schema`",
            "login",
            "password",
            "port",
            "is_encrypted",
            "is_extra_encrypted",
            "extra",
        ]
        hook = MySqlHook()
        assert (
            hook._generate_insert_sql(table="connection", values=values, target_fields=target_fields)
            == INSERT_SQL_STATEMENT
        )


DEFAULT_DATE = timezone.datetime(2015, 1, 1)
DEFAULT_DATE_ISO = DEFAULT_DATE.isoformat()
DEFAULT_DATE_DS = DEFAULT_DATE_ISO[:10]
TEST_DAG_ID = "unit_test_dag"


class MySqlContext:
    def __init__(self, client):
        self.client = client
        self.connection = MySqlHook.get_connection(MySqlHook.default_conn_name)
        self.init_client = self.connection.extra_dejson.get("client", "mysqlclient")

    def __enter__(self):
        self.connection.set_extra(f'{{"client": "{self.client}"}}')

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.connection.set_extra(f'{{"client": "{self.init_client}"}}')


@pytest.mark.backend("mysql")
@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL not available")
class TestMySql:
    def setup_method(self):
        args = {"owner": "airflow", "start_date": DEFAULT_DATE}
        dag = DAG(TEST_DAG_ID, schedule=None, default_args=args)
        self.dag = dag

    def teardown_method(self):
        drop_tables = {"test_mysql_to_mysql", "test_airflow"}
        with closing(MySqlHook().get_conn()) as conn:
            with closing(conn.cursor()) as cursor:
                for table in drop_tables:
                    cursor.execute(f"DROP TABLE IF EXISTS {table}")

    @pytest.mark.parametrize("client", ["mysqlclient", "mysql-connector-python"])
    @pytest.mark.parametrize("table", ["test_airflow", "where"])
    @mock.patch.dict(
        "os.environ",
        {
            "AIRFLOW_CONN_AIRFLOW_DB": "mysql://root@mysql/airflow?charset=utf8mb4",
        },
    )
    def test_mysql_hook_test_bulk_load(self, client, table, tmp_path):
        with MySqlContext(client):
            records = ("foo", "bar", "baz")
            path = tmp_path / "testfile"
            path.write_text("\n".join(records))

            hook = MySqlHook("airflow_db", local_infile=True)
            with closing(hook.get_conn()) as conn, closing(conn.cursor()) as cursor:
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{table}`(
                        dummy VARCHAR(50)
                    )
                """
                )
                cursor.execute(f"TRUNCATE TABLE `{table}`")
                hook.bulk_load(table, os.fspath(path))
                cursor.execute(f"SELECT dummy FROM `{table}`")
                results = tuple(result[0] for result in cursor.fetchall())
                assert sorted(results) == sorted(records)

    @pytest.mark.parametrize("client", ["mysqlclient", "mysql-connector-python"])
    @mock.patch("airflow.providers.mysql.hooks.mysql.MySqlHook.get_conn")
    def test_mysql_hook_test_bulk_dump_mock(self, mock_get_conn, client):
        with MySqlContext(client):
            mock_execute = mock.MagicMock()
            mock_get_conn.return_value.cursor.return_value.execute = mock_execute

            hook = MySqlHook("airflow_db")
            table = "INFORMATION_SCHEMA.TABLES"
            tmp_file = "/path/to/output/file"
            hook.bulk_dump(table, tmp_file)

            assert mock_execute.call_count == 1
            query = f"SELECT * INTO OUTFILE %s FROM `{table}`"
            assert_equal_ignore_multiple_spaces(mock_execute.call_args.args[0], query)
