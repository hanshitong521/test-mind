# tests package marker.
#
# 为什么必须有这个文件：某些第三方包（如 ultralytics）会把一个顶层 `tests`
# 包装进 site-packages。若本目录不是正规包，`python -m unittest tests.test_x`
# 会被 site-packages 里的 `tests` 抢先命中，报 "No module named 'tests.test_x'"。
# 有了 __init__.py，cwd（sys.path[0]）上的本包优先级更高，`tests.*` 才指向本仓。
