import os.path
import re
import shutil
import traceback
import warnings
from base64 import b64encode
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from io import BytesIO
from os import path
from typing import Dict, List

import jinja2
import markupsafe
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from dateutil import relativedelta
from django.conf import settings
from django.core.management import BaseCommand
from gcutils.bigquery import Client
from lxml import html


class Command(BaseCommand):
    help = "This command builds the prescribing outlier reports"

    def add_arguments(self, parser):
        parser.add_argument("--from_date")
        parser.add_argument("--to_date")
        parser.add_argument("--n_outliers", type=int)
        parser.add_argument("--entities", default=["practice", "ccg", "pcn", "stp"])
        parser.add_argument("--force_rebuild", default=False)
        parser.add_argument(
            "--template_path",
            default=f"{settings.TEMPLATES[0]['DIRS'][0]}/outliers/",
        )
        parser.add_argument("--url_prefix", default="")
        parser.add_argument("--n_jobs", type=int, default=3)
        parser.add_argument("--low_number_threshold", type=int, default=5)
        parser.add_argument("--entity_limit", type=int)
        parser.add_argument(
            "--output_dir",
            default=path.join(settings.PIPELINE_DATA_BASEDIR, "outlier_reports"),
        )

    def handle(self, *args, **kwargs):
        for date_param in ["from_date", "to_date"]:
            if kwargs[date_param]:
                kwargs[date_param] = datetime.strptime(
                    kwargs[date_param], "%Y-%m"
                ).date()
        if kwargs["to_date"] and not kwargs["from_date"]:
            kwargs["from_date"] = kwargs["to_date"] + relativedelta.relativedelta(
                months=-6
            )
        runner = Runner(**kwargs)
        runner.run()
        self.deploy_css(**kwargs)

    def deploy_css(self, *args, **kwargs):
        """ensure latest css files in outliers static dir"""
        for css_file in ["outliers.css", "oxford.css"]:
            shutil.copy2(
                os.path.join(settings.STATICFILES_DIRS[0], "css", css_file),
                os.path.join(kwargs["output_dir"], "html", "static", "css", css_file),
            )


class MakeHtml:
    DEFINITIONS = {
        "Chemical Items": "number of prescribed items containing this chemical",
        "Subparagraph Items": "count of all prescribed items from this subparagraph",
        "Ratio": "Ratio of chemical items to subparagraph items",
        "Mean": "Population mean number of chemical items prescribed",
        "std": "Standard Deviation",
        "Z_Score": "Number of standard deviations prescribed "
        "item count is away from the mean",
    }

    REPORT_DATE_FORMAT = "%B %Y"
    ALLCAPS = [
        "NHS",
        "PCN",
        "CCG",
        "SICBL",
        "BNF",
        "std",
        "STP",
        "(STP)",
        "ICB",
        "(ICB)",
        "NHS",
    ]
    LOW_NUMBER_CLASS = "low_number"

    @staticmethod
    def add_definitions(df):
        """
        Add html abbr/tooltip definition for column header items

        Parameters
        ----------
        df : DataFrame
            data frame to perform column replacement on
        Returns
        -------
        DataFrame
            data frame with column definitons added
        """
        return df.rename(
            columns=lambda x: (
                MakeHtml.make_abbr(x, MakeHtml.DEFINITIONS[x])
                if x in MakeHtml.DEFINITIONS
                else x
            )
        )

    @staticmethod
    def add_item_rows(table, items_df):
        """
        Adds hidden rows containing item prescription counts

        Iterates rows of html table body
        extracts BNF chemical id from each row
        filters items_df by chemical id
        creates hidden row from filtered dataframe
        rebuilds table body with visible (original) and hidden rows

        Parameters
        ----------
        table : str
            html table built from primary outlier dataframe
        items_df : DataFrame
        Returns
        -------
        table_root : str
            utf-8 encoded string of html table root element
        """

        def make_hidden_row(df, id, analyse_url):
            """
            Builds tr of precription items hidden by bootstrap collapse class

            Creates tr html element containing full with td and div within
            For each row of input df, generate BNF-item-specific analyse URL
            from input analyse_url show BNF item name and prescription count
            and add to div

            Parameters
            ----------
            df : DataFrame
                chemical, bnf_code, bnf_name, and numerator of items prescribed
            id : str
                Unique css id for tr to be built
            analyse_url : str
                URL to openprescribing anaylse page for current entity and chemical
            Returns
            -------
            tr : lxml Element
                html tr element
            """
            tr = html.Element("tr")
            tr.set("id", f"{id}_items")
            tr.set("class", "collapse")
            td = html.Element("td")
            td.set("colspan", "9")
            td.set("class", "hiddenRow")
            ul = html.Element("ul")
            ul.set("class", "my-0 ps-4 py-2")

            for i, r in df.reset_index().iterrows():
                url = analyse_url.replace(r["chemical"], r["bnf_code"])
                name = r["bnf_name"]
                count = r["numerator"]
                list_item = html.Element("li")
                anchor = html.Element("a")
                anchor.set("href", url)
                anchor.set("target", "_blank")
                anchor.text = f"{name} : {count}"
                list_item.append(anchor)
                ul.append(list_item)

            td.append(ul)
            tr.append(td)
            return tr

        def make_open_button(id):
            """
            Create open/expand prescription item detail button

            Parameters
            ----------
            id : str
                Unique css id for target tr to be expanded
            Returns
            -------
            bt_open : lxml Element
                html button element
            """
            bt_open = html.Element("button")
            bt_open.set("class", "btn btn-outline-primary btn-sm btn-xs ms-2 px-2")
            bt_open.set("data-bs-target", f"#{id}_items")
            bt_open.set("data-bs-toggle", "collapse")
            bt_open.set("type", "button")
            bt_open.text = "☰"
            return bt_open

        table_root = html.fragment_fromstring(table)
        table_id = table_root.get("id")

        hidden_rows = []
        visible_rows = table_root.xpath("tbody/tr")
        for i, tr in enumerate(visible_rows):
            # create a unique id for this row
            id = f"{table_id}_{i}"

            # hack:extract the id of the BNF chemical from the analyse URL
            analyse_url = tr.xpath("th/a")[0].get("href")
            chemical_id = analyse_url.split("/")[-1].split("&")[2].split("=")[1]

            # add an open button to the end of the first column
            tr.xpath("th")[0].append(make_open_button(id))

            hidden_rows.append(
                make_hidden_row(
                    items_df[items_df.chemical == chemical_id], id, analyse_url
                )
            )
        tbody = table_root.xpath("tbody")[0]
        tbody.drop_tree()
        tbody = html.Element("tbody")
        for hr, vr in zip(hidden_rows, visible_rows):
            tbody.append(vr)
            tbody.append(hr)
        table_root.append(tbody)
        return html.tostring(table_root).decode("utf-8")

    @staticmethod
    def format_url(df):
        """
        Replace index column values with html anchor pointing at URL in URL column
        then drop URL column

        Parameters
        ----------
        df : DataFrame
            Data frame on which to perform replacment
        Returns
        -------
        df : DataFrame
            Data Frame with index column values turned into URLs
        """

        ix_col = df.index.name
        df = df.reset_index()
        df[ix_col] = df.apply(lambda x: f'<a href="{x["URL"]}">{x[ix_col]}</a>', axis=1)
        df = df.drop("URL", axis=1)

        df = df.set_index(ix_col)

        return df

    @staticmethod
    def make_tr(thlist):
        """
        Make a 'tr' lxml Element from list of 'th' lxml Elements

        Parameters
        ----------
        thlist : list
            List of strings containing desired inner text of th Elements

        Returns
        -------
        Element
            lxml html 'tr' Element with 'th' children in order of input list
        """
        tr = html.Element("tr")
        for th in thlist:
            tr.append(th)
        return tr

    @staticmethod
    def make_abbr(text, title):
        """
        Make a 'abbr' html element from body text and its definition (title)

        Parameters
        ----------
        text : str
            Text to be definied
        title : str
            Definition for text
        Returns
        -------
        str

        """
        return f'<abbr data-bs-toggle="tooltip" data-bs-placement="top" title="{title}">{text}</abbr>'

    @staticmethod
    # hack: ideally header should be fixed in df, not html
    def merge_table_header(table):
        """
        Merge duplicate header rows of html table into one

        Replaces blank inner text of <th> in <tr>s of <thead> with non-blank
        inner text from corresponding <th>s in subsequent <tr>s

        Parameters:
        -----------
        table : str
            html <table> element containing <thead> of at least one <tr>

        Returns:
        --------
        str
            utf-8 encoded string of html <table> element
        """
        tabroot = html.fragment_fromstring(table)
        merged_th = []
        for tr in tabroot.xpath("thead/tr"):
            for i, th in enumerate(tr):
                while len(merged_th) < i + 1:
                    merged_th.append("")
                if not th.text_content() == "":
                    merged_th[i] = th
            tr.drop_tree()
        tabroot.xpath("thead")[0].append(MakeHtml.make_tr(merged_th))
        return html.tostring(tabroot).decode("utf-8")

    @staticmethod
    def df_to_html(dfs, id):
        """
        Return formatted html table from Pandas DataFrame

        Pre-formats DataFrame with URL formatting for first column,
        title-casing column headers.
        Uses native DataFrame.to_html function with selected bootstrap table
        classes, merges duplicate header rows that this generates, then
        unescapes the results using markupsafe

        Parameters
        ----------
        dfs : tuple(DataFrame)
            primary and item detail DataFrames to be converted to html
        id : css id of generated html table

        Returns
        -------
        table : str
            html fragment containing <table> element
        """
        df = dfs[0]
        items_df = dfs[1]
        df = MakeHtml.format_url(df)
        df = df.rename(columns=lambda x: MakeHtml.selective_title(x))
        df = MakeHtml.add_definitions(df)
        columns = [c for c in df.columns if c.lower() != MakeHtml.LOW_NUMBER_CLASS]
        int_format = {c: lambda x: f"{int(x):,}" for c in df.columns if "Items" in c}
        table = df.to_html(
            escape=True,
            classes=["table", "table", "table-sm", "table-bordered"],
            table_id=id,
            columns=columns,
            formatters=int_format,
        )
        table = markupsafe.Markup(table).unescape()
        table = MakeHtml.add_row_classes(df, table)
        table = MakeHtml.add_item_rows(table, items_df)
        table = MakeHtml.merge_table_header(table)

        return table

    @staticmethod
    def add_row_classes(df, table):
        """
        Adds "low_number" class to all trs with corresponding flagged rows in df
        Adds "row_{n}" class pertaining to row number to all trs

        Parameters
        ----------
        df: DataFrame
            df from which html table was created
        table: str
            html rendering of df as <table>

        Returns
        -------
        str
            modified html rendering of df as <table>
        """
        html_table = html.fragment_fromstring(table)
        rows = html_table.xpath("tbody/tr")
        for i, row in enumerate(rows):
            if df.iloc[i][MakeHtml.selective_title(MakeHtml.LOW_NUMBER_CLASS)]:
                row.classes.add(MakeHtml.LOW_NUMBER_CLASS)
            row.classes.add(f"row_{i + 1}")
        return html.tostring(html_table).decode("utf-8")

    @staticmethod
    def selective_title(str):
        """
        Convert string to Title Case except for key initialisms

        Splits input string by space character, applies Title Case to each element
        except for ["NHS", "PCN", "CCG", "BNF", "std"], then
        joins elements back together with space

        Parameters
        ----------
        str : str
            string to be selectively converted to Title Case

        Returns
        -------
        str
            Selectively title-cased string

        """
        return " ".join(
            [w.title() if w not in MakeHtml.ALLCAPS else w for w in str.split(" ")]
        )

    @staticmethod
    def write_to_template(
        entity_name,
        tables_high,
        tables_low,
        output_path,
        template_path,
        from_date: date,
        to_date: date,
        entity_type: str,
        entity_code: str,
        n_outliers: int,
    ):
        """
        Populate jinja template with outlier report data

        Calls df_to_html to generated <table> fragments,
        correctly formats entity name,
        passes these to jinja template and renders final html

        Parameters
        ----------
        entity_name : str
            Name of entity for which report is being run
        tables_high : tuple(DataFrame)
            Table of items which entity prescribes higher than average
        tables_low : tuple(DataFrame)
            Table of items which entity prescribes lower than average
        output_file : str
            file name (not full path) of html file to be written

        Returns
        -------
        str
            Complete HTML outlier report
        """

        with open(template_path) as f:
            template = jinja2.Template(f.read())

        context = {
            "entity_name": MakeHtml.selective_title(entity_name),
            "table_high": MakeHtml.df_to_html(tables_high, "table_high"),
            "table_low": MakeHtml.df_to_html(tables_low, "table_low"),
            "from_date": from_date.strftime(MakeHtml.REPORT_DATE_FORMAT),
            "to_date": to_date.strftime(MakeHtml.REPORT_DATE_FORMAT),
            "entity_type": entity_type,
            "entity_code": entity_code,
            "fmt_entity_type": MakeHtml.selective_title(entity_type.upper()),
            "n_outliers": n_outliers,
        }

        with open(output_path, "w") as f:
            f.write(template.render(context))


class DatasetBuild:
    """
    Calls outlier dataset building stored procedure on bigquery
    fetches, and encapsulates results of this process.

    Attributes
    ----------
    from_date : datetime.date
        start date of outlier reporting period
    to_date : datetime.date
        end date of outlier reporting period
    n_outliers : int
        number of outliers to include in each "high" and "low" outtlier set
    entities : List[str]
        list of column names for entity types to report e.g. "ccg"
    force_rebuild : bool
        force rebuilding of outlier dataset within bigquery and rebuilding
        of local data caches.
    numerator_column : str
        column name for numerator values in ratio calculation
        N.B: not yet integrated into bigquery stored procedure
    denominator_column : str
        column name for denominator values in ratio calculation
        N.B: not yet integrated into bigquery stored procedure
    """

    # consts
    _DATEFMT = "%Y-%m-%d"
    _KNOWN_ENTITIES = ["practice", "ccg", "pcn", "stp"]

    def __init__(
        self,
        from_date: date,
        to_date: date,
        n_outliers: int,
        entities: List[str],
        force_rebuild: bool = False,
        numerator_column: str = "chemical",
        denominator_column: str = "subpara",
    ) -> None:
        assert isinstance(to_date, date) and isinstance(from_date, date), (
            "date args must be dates"
        )
        assert to_date > from_date, "to date must be after from date"
        self.from_date = from_date
        self.to_date = to_date

        assert n_outliers > 0, "n must be greater than zero"
        self.n_outliers = n_outliers

        assert len(entities) > 0, "list of entities must be populated"
        for e in entities:
            assert e in self._KNOWN_ENTITIES, f"{e} not recognised entity"
        self.entities = entities

        self.force_rebuild = force_rebuild
        self.numerator_column = numerator_column
        self.denominator_column = denominator_column

        self.results: Dict[str, pd.DataFrame] = {}
        self.results_items: Dict[str, pd.DataFrame] = {}
        self.results_measure_arrays: Dict[str, pd.DataFrame] = {}
        self.entity_hierarchy: Dict[str, Dict[str, List[str]]] = {}

    def run(self) -> None:
        """
        Execute outlier dataset build stored procedure

        Populates build_id attribute upon completion
        """
        sql = f"""
            CALL `ebmdatalab.outlier_detection.build_outliers`(
                '{self.from_date.strftime(self._DATEFMT)}',
                '{self.to_date.strftime(self._DATEFMT)}',
                {self.n_outliers},
                {str(self.force_rebuild).upper()});
            SELECT
                build_id
            FROM
                `ebmdatalab.outlier_detection.builds`
            WHERE
                from_date = '{self.from_date.strftime(self._DATEFMT)}'
                AND to_date = '{self.to_date.strftime(self._DATEFMT)}'
                AND n ={self.n_outliers}"""
        bq_client = Client()
        res = bq_client.query_into_dataframe(sql)
        self.build_id = res["build_id"].values[0]

    def fetch_results(self) -> None:
        """
        Runs results-fetching methods for each entity type + lookup tables
        """
        assert self.build_id, "build must be run before fetching results"
        for e in self.entities:
            self._get_entity_results(e)
            self._get_entity_items(e)
            self._get_entity_measure_arrays(e)
        self._get_lookups()
        self._get_hierarchy()

    def _get_hierarchy(self) -> None:
        """
        Gets ccg-pcn-practice hierachy as dictionary
        """
        sql = """
        SELECT
            p.code as `practice_code`,
            p.pcn_id as `pcn_code`,
            p.ccg_id as `ccg_code`,
            c.stp_id as `stp_code`
        FROM
            `ebmdatalab.hscic.practices` as p
        INNER JOIN
            `ebmdatalab.hscic.ccgs` as c
            ON p.ccg_id = c.code
        WHERE
            p.setting=4
            AND status_code = 'A'
            AND p.pcn_id is not null
            AND c.stp_id is not null
        """
        bq_client = Client()
        res = bq_client.query_into_dataframe(sql)
        res = res.set_index(["stp_code", "ccg_code", "pcn_code"])

        # only include practices for which there are results
        res = res[
            res.practice_code.isin(
                self.results["practice"].index.get_level_values(0).unique()
            )
        ]

        # convert to hierarchial dict
        for stp_code in res.index.get_level_values(0).unique():
            ccgs = {}
            for ccg_code in (
                res.loc[stp_code, slice(None), slice(None)]
                .index.get_level_values(0)
                .unique()
            ):
                pcns = {}
                for pcn_code in res.loc[stp_code, ccg_code, slice(None)].index.unique():
                    pcns[pcn_code] = (
                        res.loc[stp_code, ccg_code, slice(None)]
                        .query(f"pcn_code=='{pcn_code}'")
                        .practice_code.tolist()
                    )
                ccgs[ccg_code] = pcns
            self.entity_hierarchy[stp_code] = ccgs

    def _get_lookups(self) -> None:
        """
        Fetches entity code:name mapping tables for each entity, plus
        bnf code:name mapping tables for numerator and denominator
        """
        self.names = {e: self._entity_names_query(e) for e in self.entities}
        self.names[self.numerator_column] = self._get_bnf_names(self.numerator_column)
        self.names[self.denominator_column] = self._get_bnf_names(
            self.denominator_column
        )

    def _get_entity_results(self, entity: str) -> None:
        sql = f"""
        SELECT
            {entity},
            subpara,
            subpara_items,
            chemical,
            chemical_items,
            ratio,
            mean,
            std,
            z_score,
            rank_high,
            rank_low
        FROM
            `ebmdatalab.outlier_detection.{entity}_ranked`
        WHERE
            build_id = {self.build_id}
            AND (
                    rank_high <={self.n_outliers}
                    OR rank_low <={self.n_outliers}
                );
        """

        bq_client = Client()
        res = bq_client.query_into_dataframe(sql)
        res = res.set_index([entity, self.numerator_column])
        self.results[entity] = res

    def _get_entity_items(self, entity: str) -> None:
        sql = f"""
        SELECT
            {entity},
            bnf_code,
            bnf_name,
            chemical,
            high_low,
            numerator
        FROM
            `ebmdatalab.outlier_detection.{entity}_outlier_items`
        WHERE
            build_id = {self.build_id};
        """

        bq_client = Client()
        res = bq_client.query_into_dataframe(sql)
        self.results_items[entity] = res

    def _get_entity_measure_arrays(self, entity: str) -> None:
        sql = f"""
            SELECT
                chemical,
                measure_array as `array`
            FROM
                `ebmdatalab.outlier_detection.{entity}_measure_arrays`
            WHERE
                build_id = {self.build_id};
        """
        try:
            bq_client = Client()
            res = bq_client.query_into_dataframe(sql)
        except Exception:
            print(f"Error getting BQ data for {entity}")
            traceback.print_stack()
        try:
            if not isinstance(res.iloc[0]["array"], np.ndarray):
                res["array"] = res["array"].apply(
                    lambda x: np.fromstring(x[1:-1], sep=",")
                )
            assert len(res["array"]) > 0
        except Exception:
            print(f"Error doing array conversion for {entity}")
            traceback.print_stack()
        self.results_measure_arrays[entity] = res.set_index("chemical")

    def _entity_names_query(self, entity_type: str) -> pd.DataFrame:
        """Queries the corresponding table for the entity and returns names with
        entity codes as the index

        Parameters
        ----------
        entity_type : str
            e.g. "ccg", "pcn", "practice"

        Returns
        -------
        pandas DataFrame
            code is the index and entity names are the column
        """
        sql = f"""
        SELECT
        DISTINCT {"ons_" if entity_type == "stp" else ""}code as `code`,
        name
        FROM
        ebmdatalab.hscic.{entity_type}s
        WHERE
        name IS NOT NULL
        """
        bq_client = Client()
        res = bq_client.query_into_dataframe(sql)
        return res.set_index("code")

    def _get_bnf_names(self, bnf_level: str) -> pd.DataFrame:
        """Takes in input like "chemical" and passes the appropriate fields
        to bnf_query

        Parameters
        ----------
        bnf_level : str
            BNF level, allowable values from the bnf table in BQ are:
            "chapter", "section" ,"para", "subpara" ,"chemical" ,"product",
            "presentation"

        Returns
        -------
        pandas DataFrame
            Containing bnf_code as the index and bnf name as the only column
        """
        bnf_code = f"{bnf_level}_code"
        bnf_name = bnf_level
        names = self.bnf_query(bnf_code, bnf_name)
        return names

    def bnf_query(self, bnf_code: str, bnf_name: str) -> pd.DataFrame:
        """Queries bnf table in BQ and returns a list of BNF names
        mapped to BNF codes

        Parameters
        ----------
        bnf_code : str
            name of BNF code column
        bnf_name : str
            name of BNF name column

        Returns
        -------
        pandas DataFrame
            Containing bnf_code as the index and bnf name as the only column
        """
        sql = f"""
        SELECT
        DISTINCT {bnf_code},
        {bnf_name} AS {bnf_name}_name
        FROM
        ebmdatalab.hscic.bnf
        WHERE
        {bnf_name} IS NOT NULL
        """
        bq_client = Client()
        res = bq_client.query_into_dataframe(sql)
        return res.set_index(bnf_code)


class Report:
    """
    Formatted dataset for an individual instance of an entity
    Attributes
    ----------
    build: DatasetBuild
        Instance of a built and fetched outliers dataset
    entity_type : str
        Column name for entity type, e.g. 'ccg'
    entity_code : str
        Identifying code for entity
    entity_name : str
        Name of entity
    table_high: pandas.Dataframe
        Formatted table of "high" outliers for entity
    items_high : pandas.Dataframe
        Formatted table of prescription items pertaining to high outliers
    table_low: pandas.Dataframe
        Formatted table of "low" outliers for entity
    items_low : pandas.Dataframe
        Formatted table of prescription items pertaining to low outliers
    """

    # consts
    _COL_NAMES = {
        "chapter": ["BNF Chapter", "Chapter Items"],
        "section": ["BNF Section", "Section Items"],
        "para": ["BNF Paragraph", "Paragraph Items"],
        "subpara": ["BNF Subparagraph", "Subparagraph Items"],
        "chemical": ["BNF Chemical", "Chemical Items"],
        "product": ["BNF Product", "Product Items"],
        "presentation": ["BNF Presentation", "Presentation Items"],
    }

    def __init__(
        self,
        entity_type: str,
        entity_code: str,
        build: DatasetBuild,
        low_number_threshold: int,
    ) -> None:
        self.entity_type = entity_type
        self.entity_code = entity_code
        self.build = build
        self.low_number_threshold = low_number_threshold

    def _ranked_dataset(self, h_l: str) -> pd.DataFrame:
        assert h_l in ["h", "l"], "high/low indicator must be 'h' or 'l'"
        rank_column = f"rank_{'high' if h_l == 'h' else 'low'}"
        return (
            self.build.results[self.entity_type]
            .query(f'{self.entity_type} == "{self.entity_code}"')
            .query(f"{rank_column} <= {self.build.n_outliers}")
            .copy()
            .sort_values(rank_column)
        )

    def _create_items_table(self, h_l: str) -> pd.DataFrame:
        assert h_l in ["h", "l"], "high/low indicator must be 'h' or 'l'"
        return (
            self.build.results_items[self.entity_type]
            .query(f'{self.entity_type} == "{self.entity_code}"')
            .query(f'high_low == "{h_l}"')
        )

    @staticmethod
    def _format_entity(entity: str) -> str:
        return "practice" if entity == "ccg" else entity

    @staticmethod
    def _format_denom(denominator_column: str, denominator_code: str) -> str:
        """
        formats BNF chapter/section/para/subpara strings for OP website
        e.g.: 030700 -> 3.7, 0601021 -> 6.1.2
        """
        if denominator_column in [
            "chapter",
            "section",
            "para",
            "subpara",
        ]:
            substrings = []
            for i in range(0, len(denominator_code), 2):
                sub = denominator_code[i : i + 2]
                if sub == "00" or len(sub) == 1:
                    continue
                substrings.append(sub.lstrip("0"))
            return ".".join(substrings)
        else:
            return denominator_code

    def _add_openprescribing_analyse_url(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate URL to OpenPrescribing analyse page for
        numerator and denominator highlighting entity
        Parameters
        ----------
        df : pandas df
            Input dataframe
        attr : StaticOutlierStats instance
            Contains attributes to be used in defining the tables.
        code : str
            ID of organisational entity to be highlighted

        Returns
        -------
        pandas df
            Dataframe with URL column added.
        """
        url_base = "https://openprescribing.net/analyse/#"
        url_selected = "&selectedTab=summary"

        url_org = (
            f"org={Report._format_entity(self.entity_type)}&orgIds={self.entity_code}"
        )

        def build_url(x):
            """assembles url elements in order"""
            url_num = f"&numIds={x[self.build.numerator_column]}"
            formatted_denom = Report._format_denom(
                self.build.denominator_column, x[self.build.denominator_column]
            )
            url_denom = f"&denomIds={formatted_denom}"
            return url_base + url_org + url_num + url_denom + url_selected

        ix_col = df.index.name
        df = df.reset_index()
        df["URL"] = df.apply(build_url, axis=1)
        df = df.set_index(ix_col)
        return df

    def _tidy_table(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rounds figures, drops unnecessary columns and changes column names to be
        easier to read (according to col_names), reorders columns.

        Parameters
        ----------
        df : pandas df
            Input dataframe
        attr : StaticOutlierStats instance
            Contains attributes to be used in defining the tables.

        Returns
        -------
        pandas df
            Dataframe to be passed to the HTML template writer.
        """
        df = df.round(decimals=2)
        for x in [self.build.numerator_column, self.build.denominator_column]:
            df = df.merge(self.build.names[x], how="left", left_on=x, right_index=True)
        df = df.drop(columns=[self.build.denominator_column, "rank_high", "rank_low"])
        df = df.rename(
            columns={
                f"{self.build.numerator_column}_name": self._COL_NAMES[
                    self.build.numerator_column
                ][0],
                f"{self.build.numerator_column}_items": self._COL_NAMES[
                    self.build.numerator_column
                ][1],
                f"{self.build.denominator_column}_name": self._COL_NAMES[
                    self.build.denominator_column
                ][0],
                f"{self.build.denominator_column}_items": self._COL_NAMES[
                    self.build.denominator_column
                ][1],
            }
        )
        column_order = [
            self._COL_NAMES[self.build.numerator_column][0],
            self._COL_NAMES[self.build.numerator_column][1],
            self._COL_NAMES[self.build.denominator_column][0],
            self._COL_NAMES[self.build.denominator_column][1],
            "ratio",
            "mean",
            "std",
            "z_score",
            "plots",
            "URL",
        ]
        df = df[column_order]
        df = df.set_index(self._COL_NAMES[self.build.numerator_column][0])
        return df

    def _create_out_table(self, h_l: str) -> pd.DataFrame:
        df = self._ranked_dataset(h_l)
        df = df.reset_index().set_index("chemical")
        df = df.drop(columns=self.entity_type)
        df = df.join(self.build.results_measure_arrays[self.entity_type])
        df = Plots.add_plots(df, "ratio")
        df = self._add_openprescribing_analyse_url(df)
        df = self._tidy_table(df)
        df = self._flag_low_numbers(df)

        return df

    def _flag_low_numbers(self, df):
        df[MakeHtml.LOW_NUMBER_CLASS] = df["Chemical Items"] < self.low_number_threshold
        return df

    def format(self) -> None:
        if self.entity_code in self.build.names[self.entity_type].index:
            self.entity_name = self.build.names[self.entity_type].loc[
                self.entity_code, "name"
            ]
        else:
            self.entity_name = "Unknown"
        self.table_high = self._create_out_table("h")
        self.table_low = self._create_out_table("l")
        self.items_high = self._create_items_table("h")
        self.items_low = self._create_items_table("l")


class Plots:
    """
    Collection of static methods for generation and formatting of
    distribution plots, and their appendment to dataframes
    """

    @staticmethod
    def add_plots(df, measure):
        """Use the entity values and the measure array to draw a plot for each row
        in the dataframe.

        Parameters
        ----------
        df : pandas df
            Dataframe to have plots drawn in, from create_out_table
        measure : str
            Column name to be plotted for the entity

        Returns
        -------
        pandas df
            Dataframe with added plots
        """
        df["plots"] = df[[measure, "array"]].apply(
            lambda x: Plots._html_plt(Plots._dist_plot(x[0], x[1])), axis=1
        )
        df = df.drop(columns="array")
        return df

    @staticmethod
    def _html_plt(fig):
        """Converts a matplotlib plot into an html image.

        Parameters
        ----------
        plt : matplotlib figure

        Returns
        -------
        html_plot : html image
        """
        img = BytesIO()
        fig.canvas.draw_idle()
        fig.savefig(img, transparent=True, dpi=150)
        b64_plot = b64encode(img.getvalue()).decode()
        plot_id = re.sub("[^(a-z)(A-Z)(0-9)._-]", "", b64_plot[256:288])
        html_plot = f'<button type="button" class="btn" data-bs-toggle="modal" data-bs-target="#plot_{plot_id}"><img width="250" class="h-auto" src="data:image/png;base64,{b64_plot}"/></button><div class="modal fade" id="plot_{plot_id}" tabindex="-1" aria-hidden="true"><div class="modal-dialog modal-xl"><div class="modal-content"><div class="modal-header"><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div><div class="modal-body"><img class="w-100 h-auto" src="data:image/png;base64,{b64_plot}"/></div></div></div></div>'

        return html_plot

    @staticmethod
    def _dist_plot(org_value, distribution, figsize=(3.5, 1), **kwargs):
        """Draws a matplotlib plot with a kde curve and a line for
        an individual institution.

        Parameters
        ----------
        org_value : float
            Value of the individual institution to be highlighted.
        distribution : pandas series
            Values to be used to draw the distribution.
        figsize : tuple, optional
            Size of figure. The default is (3.5, 1).
        **kwags : to be passed to plt.subplots.

        Returns
        -------
        plt : matplotlib plot
        """
        fig, ax = plt.subplots(1, 1, figsize=figsize, **kwargs)
        distribution = distribution[~np.isnan(distribution)]
        sns.kdeplot(
            distribution,
            bw_method="scott",
            ax=ax,
            linewidth=0.9,
            legend=False,
        )
        ax.axvline(org_value, color="r", linewidth=1)
        lower_limit = max(0, min(np.quantile(distribution, 0.001), org_value * 0.9))
        upper_limit = max(np.quantile(distribution, 0.999), org_value * 1.1)
        ax.set_xlim(lower_limit, upper_limit)
        ax.ticklabel_format(axis="x", style="plain")
        fig.canvas.draw_idle()
        ax = Plots._remove_clutter(ax)
        plt.close()
        return fig

    @staticmethod
    def _remove_clutter(ax):
        """Removes axes and other clutter from the charts.

        Parameters
        ----------
        ax : matplotlib axis

        Returns
        -------
        ax : matplotlib axis
        """
        for _, v in ax.spines.items():
            v.set_visible(False)
        ax.tick_params(labelsize=5)
        ax.set_yticks([])
        ax.xaxis.set_label_text("")
        ax.yaxis.set_label_text("")
        plt.tight_layout()
        return ax


class Runner:
    """
    Constructs and runs dataset build, generates report datasets,
    populates template to form html reports, builds table of contents

    Attributes
    ----------
    from_date : datetime.date
        start date of outlier reporting period
    to_date : datetime.date
        end date of outlier reporting period
    n_outliers : int
        number of outliers to include in each "high" and "low" outtlier set
    entities : List[str]
        list of column names for entity types to report e.g. "ccg"
    force_rebuild : bool
        force rebuilding of outlier dataset within bigquery and rebuilding
        of local data caches.
    entity_limit : int
        limit generated entity reports to first n of each type
    output_dir : str
        path to output directory for html report files
    template_path : str
        path to jinja2 html template for reports
    url_prefix : str
        prefix for urls for links to report files within
        generated table of contents
    low_number_threshold : int
        threshold for selectable filtering of low numbered chemical counts
    """

    def __init__(
        self,
        from_date: date,
        to_date: date,
        n_outliers: int,
        entities: List[str],
        force_rebuild: bool = False,
        entity_limit: int = None,
        output_dir="",
        template_path="",
        url_prefix="",
        n_jobs=8,
        low_number_threshold=5,
        **kwargs,
    ) -> None:
        self.build = DatasetBuild(
            from_date=from_date,
            to_date=to_date,
            n_outliers=n_outliers,
            entities=entities,
            force_rebuild=force_rebuild,
        )
        self.output_dir = output_dir
        self.template_path = template_path + "template.html"
        self.toc = TableOfContents(
            url_prefix=url_prefix,
            from_date=from_date,
            to_date=to_date,
            html_template=template_path + "toc_template.html",
        )
        self.entity_limit = entity_limit
        self.n_jobs = n_jobs
        self.low_number_threshold = low_number_threshold

    def run(self):
        # ignore numpy warnings
        np.seterr(all="ignore")

        matplotlib.use("Agg")

        # run main build process on bigquery and fetch results
        self.build.run()
        self.build.fetch_results()
        self._truncate_entities()
        self._truncate_results()
        self.toc.hierarchy = self.build.entity_hierarchy
        self.run_results = {e: [] for e in self.build.entities}

        # loop through entity types, generated a report for each entity item
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            warnings.filterwarnings("ignore", category=FutureWarning)
            warnings.filterwarnings("ignore", category=UserWarning)
            for e in self.build.entities:
                for report_result in self._run_entity_report(e):
                    self.run_results[e].append(report_result)
                    self.toc.add_item(**report_result)

        # write out toc
        self.toc.write_html(self.output_dir)
        self.toc.write_markdown(self.output_dir, True)

    def _truncate_entities(self):
        """
        Evenly discard entities throughout hierarchy so n<=limit for all levels
        """

        def stp_count():
            return len(self.build.entity_hierarchy.keys())

        def ccg_count():
            return sum([len(v.keys()) for v in self.build.entity_hierarchy.values()])

        def pcn_count():
            return sum(
                [
                    len(x.keys())
                    for y in [v.values() for v in self.build.entity_hierarchy.values()]
                    for x in y
                ]
            )

        def practice_count():
            return sum(
                [
                    len(p)
                    for q in [
                        x.values()
                        for y in [
                            v.values() for v in self.build.entity_hierarchy.values()
                        ]
                        for x in y
                    ]
                    for p in q
                ]
            )

        def one_practice_per_pcn():
            for stp, ccgs in self.build.entity_hierarchy.items():
                for ccg, pcns in ccgs.items():
                    for pcn, practices in pcns.items():
                        self.build.entity_hierarchy[stp][ccg][pcn] = practices[0:1]

        def one_pcn_per_ccg():
            for stp, ccgs in self.build.entity_hierarchy.items():
                for ccg, pcns in ccgs.items():
                    pcn = list(pcns.keys())[0]
                    self.build.entity_hierarchy[stp][ccg] = {pcn: pcns[pcn]}

        def one_ccg_per_stp():
            for stp, ccgs in self.build.entity_hierarchy.items():
                ccg = list(ccgs.keys())[0]
                self.build.entity_hierarchy[stp] = {ccg: ccgs[ccg]}

        if not self.entity_limit:
            return

        if self.entity_limit <= stp_count():
            while self.entity_limit < stp_count():
                self.build.entity_hierarchy.popitem()
            one_ccg_per_stp()
            one_pcn_per_ccg()
            one_practice_per_pcn()
            return

        if self.entity_limit <= ccg_count():
            if self.entity_limit < ccg_count():
                while True:
                    for ccgs in self.build.entity_hierarchy.values():
                        if len(ccgs) > 1:
                            ccgs.popitem()
                        if self.entity_limit == ccg_count():
                            break
                    else:
                        continue
                    break
            one_pcn_per_ccg()
            one_practice_per_pcn()
            return

        if self.entity_limit <= pcn_count():
            if self.entity_limit < pcn_count():
                while True:
                    for ccgs in self.build.entity_hierarchy.values():
                        for pcns in ccgs.values():
                            if len(pcns) > 1:
                                pcns.popitem()
                            if self.entity_limit == pcn_count():
                                break
                        else:
                            continue
                        break
                    else:
                        continue
                    break
            one_practice_per_pcn()
            return

        if self.entity_limit < practice_count():
            while True:
                for ccgs in self.build.entity_hierarchy.values():
                    for pcns in ccgs.values():
                        for practices in pcns.values():
                            if len(practices) > 1:
                                practices.pop()
                            if self.entity_limit == practice_count():
                                break
                        else:
                            continue
                        break
                    else:
                        continue
                    break
                else:
                    continue
                break

    def _truncate_results(self):
        """trims build entity results to match truncated entity hierarchy"""
        if not self.entity_limit:
            return
        stps = list(self.build.entity_hierarchy.keys())
        self.build.results["stp"] = self.build.results["stp"].loc[
            stps,
            slice(None),
        ]

        ccgs = [
            x
            for y in [v.keys() for v in self.build.entity_hierarchy.values()]
            for x in y
        ]
        self.build.results["ccg"] = self.build.results["ccg"].loc[ccgs, slice(None)]

        pcns = [
            p
            for q in [
                x.keys()
                for y in [v.values() for v in self.build.entity_hierarchy.values()]
                for x in y
            ]
            for p in q
        ]
        self.build.results["pcn"] = self.build.results["pcn"].loc[pcns, slice(None)]

        practices = [
            a
            for b in [
                p
                for q in [
                    x.values()
                    for y in [v.values() for v in self.build.entity_hierarchy.values()]
                    for x in y
                ]
                for p in q
            ]
            for a in b
        ]

        self.build.results["practice"] = self.build.results["practice"].loc[
            practices, slice(None)
        ]

    def _run_item_report(self, entity, code):
        report = Report(
            entity_type=entity,
            entity_code=code,
            build=self.build,
            low_number_threshold=self.low_number_threshold,
        )
        report.format()
        output_file = path.join(
            self.output_dir,
            "html",
            f"static_{entity}_{code}.html",
        )

        MakeHtml.write_to_template(
            entity_name=report.entity_name,
            tables_high=(report.table_high, report.items_high),
            tables_low=(report.table_low, report.items_low),
            output_path=output_file,
            template_path=self.template_path,
            from_date=self.build.from_date,
            to_date=self.build.to_date,
            entity_type=entity,
            entity_code=code,
            n_outliers=report.build.n_outliers,
        )
        return {
            "code": code,
            "name": report.entity_name,
            "entity": entity,
            "file_path": output_file,
        }

    def _run_entity_report(self, entity):
        codes = self.build.results[entity].index.get_level_values(0).unique()
        with ProcessPoolExecutor(max_workers=self.n_jobs) as pool:
            futures = [
                pool.submit(self._run_item_report, entity=entity, code=code)
                for code in codes
            ]
        results = []
        exceptions = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as e:
                exceptions.append(e)
        return results


class TableOfContents:
    """
    Builds Markdown or html table of contents

    Attributes
    ----------
    hierarchy : Dict[]
        hierarchical dictionary of entity codes
    urlprefix : str, optional
        prefix to be appended to the URL of each item
        added to the table of contents
    heading : str, optional
        override standard "Table of Contents" header
    html_template : str, optional
        path to jinja2 template file for html output
    """

    def __init__(
        self,
        url_prefix,
        from_date: date,
        to_date: date,
        heading="Table of contents",
        html_template="../data/toc_template.html",
    ):
        self.items = {}
        self.hierarchy = {}
        self.url_prefix = url_prefix
        self.heading = heading
        self.html_template = html_template
        self.from_date = from_date
        self.to_date = to_date

    def add_item(self, code, name, file_path, entity=""):
        """
        Adds a file to the table of contents

        Parameters
        ----------
        path : str
            relative path to the item
        entity : str, optional
            entity type of the item
        """
        if entity not in self.items.keys():
            self.items[entity] = {code: {"name": name, "file_path": file_path}}
        else:
            self.items[entity][code] = {"name": name, "file_path": file_path}

    def _get_context(self, output_path):
        ctx = {"header": self.heading}
        ctx["from_date"] = self.from_date.strftime(MakeHtml.REPORT_DATE_FORMAT)
        ctx["to_date"] = self.to_date.strftime(MakeHtml.REPORT_DATE_FORMAT)
        ctx["stps"] = []
        for stp_code, ccgs in self.hierarchy.items():
            stp_item = self._get_item_context(stp_code, "stp", output_path)
            stp_item["ccgs"] = []
            for ccg_code, pcns in ccgs.items():
                ccg_item = self._get_item_context(ccg_code, "ccg", output_path)
                ccg_item["pcns"] = []
                for pcn_code, practices in pcns.items():
                    pcn_item = self._get_item_context(pcn_code, "pcn", output_path)
                    pcn_item["practices"] = []
                    for practice_code in practices:
                        pcn_item["practices"].append(
                            self._get_item_context(
                                practice_code, "practice", output_path
                            )
                        )
                    pcn_item["practices"].sort(key=lambda x: x["name"])
                    ccg_item["pcns"].append(pcn_item)
                ccg_item["pcns"].sort(key=lambda x: x["name"])
                stp_item["ccgs"].append(ccg_item)
            stp_item["ccgs"].sort(key=lambda x: x["name"])
            ctx["stps"].append(stp_item)
        ctx["stps"].sort(key=lambda x: x["name"])
        return ctx

    def _get_item_context(self, entity_code, entity_type, output_path):
        entity_item = self.items[entity_type][entity_code]
        return {
            "code": entity_code,
            "name": MakeHtml.selective_title(entity_item["name"]),
            "href": self.url_prefix
            + self._full_path(
                # output_path,
                "/",
                self._relative_path(output_path, entity_item["file_path"]),
            ),
        }

    def write_html(self, output_path):
        """
        Write table of contents as html to index.html in output path

        Parameters
        ----------
        output_path : str
            directory to write markdown, items are assumed to be in same
            or sub-directory for relative path derivation
        """
        assert self.items, "no items to write"
        with open(self.html_template) as f:
            template = jinja2.Template(f.read())

        context = self._get_context(output_path)

        with open(os.path.join(output_path, "index.html"), "w") as f:
            f.write(template.render(context))

    def write_markdown(self, output_path, link_to_html_toc=False):
        """
        Write table of contents as Markdown to README.md in output path

        Parameters
        ----------
        output_path : str
            directory to write markdown, items are assumed to be in same
            or sub-directory for relative path derivation
        """
        assert self.items, "no items to write"
        tocfile = output_path + "/README.md"
        with open(tocfile, "w") as f:
            if link_to_html_toc:
                f.write(self._print_markdown_link_html())
            else:
                f.write(self._print_markdown(output_path))

    def _print_markdown_link_html(self):
        html_toc_url = f"{self.url_prefix}{'index.html'}"
        return f"# [{self.heading}]({html_toc_url})"

    def _print_markdown(self, output_path):
        """
        Renders table of contents as Markdown

        Derives path of each item relative to the output path by finding
        common parent directories and removing. If your item paths and
        output paths do not have the same base then this may
        produce unexpected behaviour.

        Parameters
        ----------
        output_path : str
            directory where markdown file will eventually be written
            items are assumed to be in same,
            or sub-directory for relative path derivation
        Returns
        -------
        toc : str
            table of contents in Markdown format
        """
        toc = f"# {self.heading}"
        for entity in self.items.keys():
            toc = toc + "\n" + f"* {entity}"
            for _, v in self.items[entity].items():
                file = v["file_path"]
                relative_path = self._relative_path(output_path, file)
                fullpath = self._full_path(relative_path)
                toc = toc + (
                    "\n" + f"  * [{relative_path}]({self.url_prefix}{fullpath})"
                )
        return toc

    @staticmethod
    def _relative_path(output_path, file_path) -> str:
        common_path = os.path.commonpath([output_path, file_path])
        path_parts = TableOfContents._split_all(file_path)
        for cp in TableOfContents._split_all(common_path):
            path_parts.remove(cp)
        return os.path.join(*path_parts)

    @staticmethod
    def _full_path(output_path, relative_path) -> str:
        return os.path.join(os.path.basename(output_path), relative_path)

    @staticmethod
    def _split_all(path):
        """
        splits a path into a list of all its elements using the local separator

        adapted from
        https://www.oreilly.com/library/view/python-cookbook/0596001673/ch04s16.html

        Parameters
        ----------
        path : str
            path to split, OS agnostic
        Returns
        -------
        allparts : list
            list of all parts of the path as strings
        """
        allparts = []
        while 1:
            parts = os.path.split(path)
            if parts[0] == path:  # sentinel for absolute paths
                allparts.insert(0, parts[0])
                break
            elif parts[1] == path:  # sentinel for relative paths
                allparts.insert(0, parts[1])
                break
            else:
                path = parts[0]
                allparts.insert(0, parts[1])
        return allparts
