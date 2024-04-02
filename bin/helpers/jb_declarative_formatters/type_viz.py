from jb_declarative_formatters.type_name_template import TypeNameTemplate
from jb_declarative_formatters.type_viz_expression import get_custom_view_spec_id_by_name


class TypeVizName(object):
    def __init__(self, type_name, type_name_template: TypeNameTemplate):
        self.type_name = type_name
        self.type_name_template = type_name_template

    @property
    def has_wildcard(self):
        return self.type_name_template.has_wildcard

    def __str__(self):
        return self.type_name


class TypeViz(object):
    def __init__(self, type_viz_names, is_inheritable, include_view: str, exclude_view: str, priority, logger=None):
        self.logger = logger  # TODO: or stub

        self.type_viz_names = type_viz_names  # list[TypeVizName]
        self.is_inheritable = is_inheritable
        self.include_view = include_view
        self.include_view_id = get_custom_view_spec_id_by_name(include_view)
        self.exclude_view = exclude_view
        self.exclude_view_id = get_custom_view_spec_id_by_name(exclude_view)
        self.priority = priority
        self.summaries = []
        self.item_providers = None
