# flake8: noqa
import csv


def model_name(table_name):
    if table_name in ["vtm", "vpi", "vmp", "vmpp", "amp", "ampp", "gtin"]:
        return table_name.upper()
    else:
        return "".join(tok.title() for tok in table_name.split("_"))


def quote(s):
    assert '"' not in s
    return '"' + s + '"'


with open("schema.csv") as f:
    lines = list(csv.DictReader(f))


print("from django.db import models")

table = None

for line in lines:
    if line["table"] == "ccontent":
        continue

    if line["table"] != table:
        table = line["table"]
        print()
        print()
        print(f"class {model_name(table)}(models.Model):")
        print("#    class Meta:")
        print('#        verbose_name = "TODO"')
        print()

    if line["type"] == "retired":
        continue

    options = []

    if line["primary_key"] == "True":
        options.append(("primary_key", "True"))

    if line["db_column"]:
        options.append(("db_column", quote(line["db_column"])))

    if line["type"] in ["ForeignKey", "OneToOneField"]:
        options.append(("to", quote(model_name(line["to"]))))
        options.append(("on_delete", "models.CASCADE"))

        if "prevcd" in line["db_column"] or "uomcd" in line["db_column"]:
            options.append(("related_name", quote("+")))

    elif line["type"] == "CharField":
        options.append(("max_length", line["max_length"]))

    elif line["type"] == "DecimalField":
        options.append(("max_digits", line["max_digits"]))
        options.append(("decimal_places", line["decimal_places"]))

    if line["optional"] == "Y":
        if line["type"] != "BooleanField" and line["primary_key"] != "True":
            options.append(("null", "True"))

    options.append(("help_text", quote(line["descr"])))

    print(f"    {line['field']} = models.{line['type']}(")

    for k, v in options:
        print(f"        {k}={v},")

    print("    )")
