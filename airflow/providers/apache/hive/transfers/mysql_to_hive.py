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

"""This module contains an operator to move data from MySQL to Hive."""

from collections import OrderedDict
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Dict, Optional, Sequence

import MySQLdb
import unicodecsv as csv

from airflow.models import BaseOperator
from airflow.providers.apache.hive.hooks.hive import HiveCliHook
from airflow.providers.mysql.hooks.mysql import MySqlHook

if TYPE_CHECKING:
    from airflow.utils.context import Context


class MySqlToHiveOperator(BaseOperator):
    """
    Moves data from MySql to Hive. The operator runs your query against
    MySQL, stores the file locally before loading it into a Hive table.
    If the ``create`` or ``recreate`` arguments are set to ``True``,
    a ``CREATE TABLE`` and ``DROP TABLE`` statements are generated.
    Hive data types are inferred from the cursor's metadata. Note that the
    table generated in Hive uses ``STORED AS textfile``
    which isn't the most efficient serialization format. If a
    large amount of data is loaded and/or if the table gets
    queried considerably, you may want to use this operator only to
    stage the data into a temporary table before loading it into its
    final destination using a ``HiveOperator``.

    :param sql: SQL query to execute against the MySQL database. (templated)
    :type sql: str
    :param hive_table: target Hive table, use dot notation to target a
        specific database. (templated)
    :type hive_table: str
    :param create: whether to create the table if it doesn't exist
    :type create: bool
    :param recreate: whether to drop and recreate the table at every
        execution
    :type recreate: bool
    :param partition: target partition as a dict of partition columns
        and values. (templated)
    :type partition: dict
    :param delimiter: field delimiter in the file
    :type delimiter: str
    :param quoting: controls when quotes should be generated by csv writer,
        It can take on any of the csv.QUOTE_* constants.
    :type quoting: str
    :param quotechar: one-character string used to quote fields
        containing special characters.
    :type quotechar: str
    :param escapechar: one-character string used by csv writer to escape
        the delimiter or quotechar.
    :type escapechar: str
    :param mysql_conn_id: source mysql connection
    :type mysql_conn_id: str
    :param hive_cli_conn_id: Reference to the
        :ref:`Hive CLI connection id <howto/connection:hive_cli>`.
    :type hive_cli_conn_id: str
    :param tblproperties: TBLPROPERTIES of the hive table being created
    :type tblproperties: dict
    """

    template_fields: Sequence[str] = ('sql', 'partition', 'hive_table')
    template_ext: Sequence[str] = ('.sql',)
    ui_color = '#a0e08c'

    def __init__(
        self,
        *,
        sql: str,
        hive_table: str,
        create: bool = True,
        recreate: bool = False,
        partition: Optional[Dict] = None,
        delimiter: str = chr(1),
        quoting: Optional[str] = None,
        quotechar: str = '"',
        escapechar: Optional[str] = None,
        mysql_conn_id: str = 'mysql_default',
        hive_cli_conn_id: str = 'hive_cli_default',
        tblproperties: Optional[Dict] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.sql = sql
        self.hive_table = hive_table
        self.partition = partition
        self.create = create
        self.recreate = recreate
        self.delimiter = str(delimiter)
        self.quoting = quoting or csv.QUOTE_MINIMAL
        self.quotechar = quotechar
        self.escapechar = escapechar
        self.mysql_conn_id = mysql_conn_id
        self.hive_cli_conn_id = hive_cli_conn_id
        self.partition = partition or {}
        self.tblproperties = tblproperties

    @classmethod
    def type_map(cls, mysql_type: int) -> str:
        """Maps MySQL type to Hive type."""
        types = MySQLdb.constants.FIELD_TYPE
        type_map = {
            types.BIT: 'INT',
            types.DECIMAL: 'DOUBLE',
            types.NEWDECIMAL: 'DOUBLE',
            types.DOUBLE: 'DOUBLE',
            types.FLOAT: 'DOUBLE',
            types.INT24: 'INT',
            types.LONG: 'BIGINT',
            types.LONGLONG: 'DECIMAL(38,0)',
            types.SHORT: 'INT',
            types.TINY: 'SMALLINT',
            types.YEAR: 'INT',
            types.TIMESTAMP: 'TIMESTAMP',
        }
        return type_map.get(mysql_type, 'STRING')

    def execute(self, context: "Context"):
        hive = HiveCliHook(hive_cli_conn_id=self.hive_cli_conn_id)
        mysql = MySqlHook(mysql_conn_id=self.mysql_conn_id)

        self.log.info("Dumping MySQL query results to local file")
        conn = mysql.get_conn()
        cursor = conn.cursor()
        cursor.execute(self.sql)
        with NamedTemporaryFile("wb") as f:
            csv_writer = csv.writer(
                f,
                delimiter=self.delimiter,
                quoting=self.quoting,
                quotechar=self.quotechar,
                escapechar=self.escapechar,
                encoding="utf-8",
            )
            field_dict = OrderedDict()
            for field in cursor.description:
                field_dict[field[0]] = self.type_map(field[1])
            csv_writer.writerows(cursor)
            f.flush()
            cursor.close()
            conn.close()
            self.log.info("Loading file into Hive")
            hive.load_file(
                f.name,
                self.hive_table,
                field_dict=field_dict,
                create=self.create,
                partition=self.partition,
                delimiter=self.delimiter,
                recreate=self.recreate,
                tblproperties=self.tblproperties,
            )
