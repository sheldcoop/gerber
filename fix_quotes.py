with open('core/svg_utils.py', 'r') as f:
    code = f.read()

code = code.replace('\\"\\"\\"', '\"\"\"')

with open('core/svg_utils.py', 'w') as f:
    f.write(code)
