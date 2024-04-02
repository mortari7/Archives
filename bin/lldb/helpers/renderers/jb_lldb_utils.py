from typing import Optional

import lldb
from renderers.jb_lldb_declarative_formatters_options import set_recursion_level
from renderers.jb_lldb_format_specs import eFormatRawView
from renderers.jb_lldb_logging import log
from six import StringIO


class EvaluateError(Exception):
    def __init__(self, error):
        super(Exception, self).__init__(str(error))


class IgnoreSynthProvider(Exception):
    def __init__(self, msg=None):
        super(Exception, self).__init__(str(msg) if msg else None)


class Stream(object):
    def __init__(self, is64bit: bool, initial_level: int):
        self.stream = StringIO()
        self.pointer_format = "0x{:016x}" if is64bit else "0x{:08x}"
        self.length = 0
        self.level = initial_level

    def create_nested(self):
        val = self.__class__(False, self.level)
        val.pointer_format = self.pointer_format
        val.length = self.length
        return val

    def output(self, text):
        self.length += len(text)
        self.stream.write(text)

    def output_object(self, val_non_synth: lldb.SBValue):
        log("Retrieving summary of value named '{}'...", val_non_synth.GetName())

        val_type = val_non_synth.GetType()
        format_spec = val_non_synth.GetFormat()
        use_raw_viz = format_spec & eFormatRawView
        provider = get_viz_descriptor_provider()
        vis_descriptor = provider.get_matched_visualizers(val_type, use_raw_viz)

        self.level += 1
        prev_level = set_recursion_level(self.level)
        try:
            if vis_descriptor is not None:
                try:
                    vis_descriptor.output_summary(val_non_synth, self)
                except Exception as e:
                    log('Internal error: {}', str(e))

            else:
                self._output_object_fallback(provider, val_non_synth, val_type)
        finally:
            set_recursion_level(prev_level)
            self.level -= 1

    def _output_object_fallback(self, provider, val_non_synth, val_type):
        # force use raw vis descriptor
        vis_descriptor = provider.get_matched_visualizers(val_type, True)
        if vis_descriptor is not None:
            try:
                vis_descriptor.output_summary(val_non_synth, self)
            except Exception as e:
                log('Internal error: {}', str(e))
        else:
            summary_value = val_non_synth.GetValue() or ''
            self.output(summary_value)

    def output_string(self, text: str):
        self.output(text)

    def output_keyword(self, text: str):
        self.output(text)

    def output_number(self, text: str):
        self.output(text)

    def output_comment(self, text: str):
        self.output(text)

    def output_value(self, text: str):
        self.output(text)

    def output_address(self, address: int):
        self.output_comment(self.pointer_format.format(address))

    def __str__(self):
        return self.stream.getvalue()


INVALID_CHILD_INDEX = 2 ** 32 - 1


class AbstractChildrenProvider(object):
    def num_children(self):
        return 0

    def get_child_index(self, name):
        return INVALID_CHILD_INDEX

    def get_child_at_index(self, index):
        return None


g_empty_children_provider = AbstractChildrenProvider()


class AbstractVisDescriptor(object):
    def output_summary(self, value_non_synth: lldb.SBValue, stream: Stream):
        pass

    def prepare_children(self, value_non_synth: lldb.SBValue) -> AbstractChildrenProvider:
        return g_empty_children_provider


class AbstractVizDescriptorProvider(object):
    def get_matched_visualizers(self, value_type: lldb.SBType, raw_visualizer: bool) -> AbstractVisDescriptor:
        pass


g_viz_descriptor_provider: AbstractVizDescriptorProvider


def get_viz_descriptor_provider() -> AbstractVizDescriptorProvider:
    return g_viz_descriptor_provider


def set_viz_descriptor_provider(provider: AbstractVizDescriptorProvider):
    global g_viz_descriptor_provider
    g_viz_descriptor_provider = provider


class FormattedStream(Stream):
    def output_string(self, text):
        self.stream.write("\xfeS")
        self.output(text)
        self.stream.write("\xfeE")

    def output_keyword(self, text):
        self.stream.write("\xfeK")
        self.output(text)
        self.stream.write("\xfeE")

    def output_number(self, text):
        self.stream.write("\xfeN")
        self.output(text)
        self.stream.write("\xfeE")

    def output_comment(self, text):
        self.stream.write("\xfeC")
        self.output(text)
        self.stream.write("\xfeE")

    def output_value(self, text):
        self.stream.write("\xfeV")
        self.output(text)
        self.stream.write("\xfeE")


def make_absolute_name(root, name):
    return '.'.join([root, name])


def register_lldb_commands(debugger, cmd_map):
    for func, cmd in cmd_map.items():
        debugger.HandleCommand('command script add -f {func} {cmd}'.format(func=func, cmd=cmd))


class EvaluationContext(object):
    def __init__(self, prolog: str, epilog: str, context_variables: Optional[lldb.SBValueList]):
        self.prolog_code: str = prolog
        self.epilog_code: str = epilog
        self.context_variables: Optional[lldb.SBValueList] = context_variables


def eval_expression(val: lldb.SBValue, expr: str, value_name: Optional[str],
                    context: Optional[EvaluationContext] = None) -> lldb.SBValue:
    log("Evaluate '{}' in context of '{}' of type '{}'", expr, val.GetName(), val.GetTypeName())

    if "__findnonnull" in expr:
        findnonnull = """#define __findnonnull(PTR, SIZE) [&](decltype(PTR) ptr, decltype(SIZE) size){\\
                for (int i = 0; i < size; ++ i)\\
                    if (ptr[i] != nullptr)\\
                        return i;\\                                    
                return -1;\\
            }(PTR, SIZE)
            """
    else:
        findnonnull = ""

    if context:
        format_string = "{}{}; auto&& __lldb__result__ = ({}); {}; __lldb__result__;"
        code = format_string.format(findnonnull, context.prolog_code, expr, context.epilog_code)
    elif findnonnull != "":
        code = findnonnull + expr
    else:
        code = expr

    err = lldb.SBError()
    options = lldb.SBExpressionOptions()
    options.SetSuppressPersistentResult(True)
    options.SetFetchDynamicValue(lldb.eDynamicDontRunTarget)
    result = val.EvaluateExpression(code, options, value_name)
    if result is None:
        err.SetErrorString("evaluation setup failed")
        log("Evaluate failed: {}", str(err))
        raise EvaluateError(err)

    result_non_synth = result.GetNonSyntheticValue()
    err: lldb.SBError = result_non_synth.GetError()
    if err.Fail():
        err_type = err.GetType()
        err_code = err.GetError()
        if err_type == lldb.eErrorTypeExpression and err_code == lldb.eExpressionParseError:
            log("Evaluate failed (can't parse expression): {}", str(err))
            raise EvaluateError(err)

        # error is runtime error which is handled later
        log("Returning value with error: {}", str(err))
        return result

    log("Evaluate succeed: result type - {}", str(result_non_synth.GetTypeName()))
    return result


def get_root_value(val: lldb.SBValue) -> lldb.SBValue:
    val_non_synth: lldb.SBValue = val.GetNonSyntheticValue()
    val_non_synth.SetPreferDynamicValue(lldb.eNoDynamicValues)
    return val_non_synth


def get_value_format(val: lldb.SBValue) -> int:
    return get_root_value(val).GetFormat()


def set_value_format(val: lldb.SBValue, fmt: int):
    get_root_value(val).SetFormat(fmt)
