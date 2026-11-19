Configuration
=============

The pipeline file (one YAML file per pipeline in the store folder), its
templates, and what turns a file into a run's config: the generic resolver that
lays the file's ``params.<module>`` over each listed module's own defaults and
validates the result with the module's options model, and the frozen run config
(``InternalConfig``) that records every listed module's options. The options
themselves are defined in the modules (``adapt.modules.<name>.config``), not
here.

.. automodule:: adapt.configuration.pipeline_file
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: adapt.configuration.pipeline_run
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: adapt.configuration.pipeline_template
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: adapt.configuration.schemas.internal
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: adapt.configuration.schemas.module_resolver
   :members:
   :undoc-members:
   :show-inheritance:
