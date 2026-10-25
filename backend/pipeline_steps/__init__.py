"""Built-in pipeline steps (summary statistics, SVG charts).

Each file here is a standalone script (invoked as a subprocess by the
executor, not imported), so a step is exactly "a command plus some files" --
no hidden coupling to the pipeline package itself.
"""
