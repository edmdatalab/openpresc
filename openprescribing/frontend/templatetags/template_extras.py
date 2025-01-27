import math

from django import template
from django.contrib.humanize.templatetags.humanize import intcomma
from django.contrib.staticfiles.storage import staticfiles_storage
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def conditional_js(context, script_name):
    suffix = "" if context.get("DEBUG", True) else ".min"
    filename = "js/{}{}.js".format(script_name, suffix)
    url = staticfiles_storage.url(filename)
    tag = '<script src="{}"></script>'.format(url)
    return mark_safe(tag)


@register.filter
def wholenum(num):
    return int(round(num))


@register.filter
def deltawords(num, arg):
    """An adverb to come after the word 'improved' or 'slipped'"""
    delta = abs(num - arg)
    # We only pick out changes over 10%; over 30% in 9 months is unheard of.
    if delta == 0:
        word = "not at all"
    elif delta < 10:
        word = "slightly"
    elif delta < 20:
        word = "moderately"
    elif delta < 30:
        word = "considerably"
    else:
        word = "massively"
    return word


@register.filter
def roundpound(num):
    order = 10 ** math.floor(math.log10(num))
    if order > 0:
        return intcomma(int(round(num / order) * order))
    else:
        return str(int(round(num)))


@register.filter
def sigfigs(value, figures=3):
    """
    Round value to supplied significant figures
    """
    if not value:
        # This might happen when testing.
        value = 0

    if value == 0:
        order = 0
    else:
        order = int(math.floor(math.log10(math.fabs(value))))

    places = figures - order - 1
    format_string = "{:.%df}" % max(0, places)
    return format_string.format(round(value, places))


@register.simple_tag
def url_toggle(request, field):
    dict_ = request.GET.copy()
    if field in dict_:
        del dict_[field]
    else:
        dict_[field] = 1
    return dict_.urlencode()


@register.simple_tag
def current_time(format_string):
    return timezone.now().strftime(format_string)


@register.filter
def fancy_join(lst, sep=", ", final_sep=" and "):
    """
    Join a list using a different separator for the final element
    """
    if len(lst) > 2:
        head, tail = lst[:-1], lst[-1]
        lst = [sep.join(head), tail]
    return final_sep.join(lst)


@register.filter
def username_from_email(email):
    return email.split("@")[0]


@register.simple_tag(takes_context=True)
def dashboard_measure_uri(context, measure):
    return format_html(
        "{}&tags={}#{}",
        context["dashboard_uri"],
        # It doesn't matter which tag we chose, it just needs to be one that the target
        # measure has so we know it will appear on the linked dashboard
        measure.tags[0],
        measure.id,
    )
