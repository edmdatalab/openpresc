import csv
import os
from pipes import quote
import subprocess


class InvalidHeaderError(Exception):
    pass


def sort_and_merge_gzipped_csv_files(
    # CSV files to sort (which may or may not be gzipped)
    input_filenames,
    # Output file
    output_filename,
    # Column names to sort by
    sort_columns,
):
    """
    Given a list of CSV files, sort the rows by the supplied column names and
    write the result to `output_filename`

    Input files may be gzipped or not (either will work). The output is
    always gzipped.

    We shell out to the `sort` command for this as it much more efficient than
    trying to do this in Python and can transparently handle sorting files that
    are many times too large to fit in memory.

    Note that `sort` doesn't really parse CSV, it just splits on commas; so
    this function won't work where the CSV contains commas -- at least, where
    these are to the left of the columns which are being sorted on.
    """
    header_line = get_header_line(input_filenames)
    sort_column_indices = get_column_indices(header_line, sort_columns)
    # Construct a shell pipeline to read all input files and sort in the
    # correct order, outputing the header line first
    pipeline = "( {read_files} ) | ( echo {header_line}; {sort_by_columns} )".format(
        read_files=read_files(input_filenames, skip_lines=1),
        header_line=quote(header_line),
        sort_by_columns=sort_by_columns(sort_column_indices),
    )
    pipeline += " | gzip"
    pipeline += " > {}".format(quote(output_filename))
    env = os.environ.copy()
    # For much faster string comparison when sorting
    env["LANG"] = "C"
    subprocess.check_call(pipeline, shell=True, env=env)


def read_files(filenames, skip_lines=None, max_lines=None):
    """
    Return command to read all supplied files (which may or may not be
    gzipped), optionally skipping a number of leading and trailing lines
    """
    return "; ".join(
        [
            read_file(filename, skip_lines=skip_lines, max_lines=max_lines)
            for filename in filenames
        ]
    )


def read_file(filename, skip_lines=None, max_lines=None):
    """
    Return command to read a file (which may or may not be gzipped), optionally
    skipping a number of leading and trailing lines
    """
    # The `--force` flag means that non-gzipped files are handled transparently
    # as if the command was just `cat`
    command = "gzip --decompress --force --to-stdout --quiet {}".format(quote(filename))
    if skip_lines is not None:
        command += " | tail -n +{}".format(int(skip_lines) + 1)
    if max_lines is not None:
        command += " | head -n {}".format(int(max_lines))
    return command


def sort_by_columns(column_indices):
    """
    Return a `sort` command string configured to sort a CSV file by the supplied column
    indices
    """
    sort_keys = ["--key={0},{0}".format(i + 1) for i in column_indices]
    return "sort --field-separator=, {}".format(" ".join(sort_keys))


def get_header_line(filenames):
    """
    Return the first line of one of the files and check it is consistent across
    all files
    """
    pipeline = "({read_files}) 2>/dev/null".format(
        read_files=read_files(filenames, max_lines=1)
    )
    header_lines = (
        subprocess.check_output(pipeline, shell=True).decode("utf8").splitlines()
    )
    header_line = header_lines[0]
    for n, filename in enumerate(filenames):
        other_line = header_lines[n]
        if other_line != header_line:
            raise InvalidHeaderError(
                "Input files do not have identical headers:\n\n{}: {}\n{}: {}".format(
                    filenames[0], header_line, filename, other_line
                )
            )
    return header_line


def get_column_indices(header_line, columns):
    """
    Take a CSV header line and a list of columns and return the indices of
    those columns (or raise InvalidHeaderError)
    """
    headers = next(csv.reader([header_line]))
    try:
        return [headers.index(column) for column in columns]
    except ValueError as e:
        raise InvalidHeaderError("{} of headers: {}".format(e, header_line))
