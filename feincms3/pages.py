from collections import OrderedDict

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Max, Q
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from cte_forest.models import CTENode, CTENodeManager
from feincms3.utils import validation_error


class AbstractPageManager(CTENodeManager):
    """
    Defines a single method, ``active``, which only returns pages with
    ``is_active=True``.
    """
    def active(self):
        """
        Return only active pages

        This function is used in :func:`~feincms3.apps.apps_urlconf` and
        in the bundled ``menu`` template tag.
        """
        return self.filter(is_active=True)


class AbstractPage(CTENode):
    """
    Short version: If you want to build a CMS with a hierarchical page
    structure, use this base class.

    It comes with the following fields:

    - ``parent``: (a nullable tree foreign key) and a ``position`` field for
      relatively ordering pages.
    - ``is_active``: Boolean field. The ``save()`` method ensures that inactive
      pages never have any active descendants.
    - ``title`` and ``slug``
    - ``path``: The complete path to the page, starting and ending with a
      slash. The maximum length of path (1000) should be enough for everyone
      (tm, famous last words). This field also has a unique index, which means
      that MySQL with its low limit on unique indexes will not work with this
      base class. Sorry.
    - ``static_path``: A boolean which, when ``True``, allows you to fill in
      the ``path`` field all by yourself. By default, ``save()`` ensures that
      the ``path`` fields are always composed of a concatenation of the
      parent's ``path`` with the page's own ``slug`` (with slashes of course).
      This is especially useful for root pages (set ``path`` to ``/``) or,
      when building a multilingual site, for language root pages (i.e.
      ``/en/``, ``/de/``, ``/pt-br/`` etc.)
    """
    _cte_node_path = 'cte_path'
    _cte_node_order_by = ('position',)

    is_active = models.BooleanField(
        _('is active'),
        default=True,
    )
    title = models.CharField(
        _('title'),
        max_length=200,
    )
    slug = models.SlugField(
        _('slug'),
    )
    position = models.PositiveIntegerField(
        db_index=True,
        editable=False,
        default=0,
    )

    # Who even cares about MySQL
    path = models.CharField(
        _('path'),
        max_length=1000,
        blank=True,
        unique=True,
        help_text=_('Generated automatically if \'static path\' is unset.'),
        validators=[
            RegexValidator(
                regex=r'^/(|.+/)$',
                message=_('Path must start and end with a slash (/).'),
            ),
        ],
    )
    static_path = models.BooleanField(
        _('static path'),
        default=False,
    )

    objects = AbstractPageManager()

    class Meta:
        abstract = True
        verbose_name = _('page')
        verbose_name_plural = _('pages')

    def __str__(self):
        return self.title

    def _branch_for_update(self):
        nodes = OrderedDict({self.pk: self})
        for node in self.descendants():
            # Assign already-updated instance:
            node.parent = nodes[node.parent_id]
            if not node.static_path:
                node.path = '%s%s/' % (
                    node.parent.path,
                    node.slug,
                )

            # Descendants of inactive nodes cannot be active themselves:
            if not node.parent.is_active:
                node.is_active = False
            nodes[node.id] = node
        return nodes

    def clean_fields(self, exclude=None):
        """
        Check for path uniqueness problems.
        """
        super(AbstractPage, self).clean_fields(exclude)

        if self.static_path:
            if not self.path:
                raise validation_error(
                    _('Static paths cannot be empty. Did you mean \'/\'?'),
                    field='path',
                    exclude=exclude,
                )
        else:
            self.path = '%s%s/' % (
                self.parent.path if self.parent else '/',
                self.slug,
            )

        super(AbstractPage, self).clean()

        # Skip if we don't exist yet.
        if not self.pk:
            return

        clash_candidates = self.__class__._default_manager.exclude(
            Q(pk__in=self.descendants()) | Q(pk=self.pk),
        )
        for pk, node in self._branch_for_update().items():
            if clash_candidates.filter(path=node.path).exists():
                raise validation_error(
                    _(
                        'The page %(page)s\'s new path %(path)s would'
                        ' not be unique.'
                    ) % {
                        'page': node,
                        'path': node.path,
                    },
                    field='path',
                    exclude=exclude,
                )

    def save(self, *args, **kwargs):
        """save(self, ..., save_descendants=True)
        Saves the page instance, and traverses all descendants to update their
        ``path`` fields and ensure that inactive pages (``is_active=False``)
        never have any descendants with ``is_active=True``.
        """
        save_descendants = kwargs.pop('save_descendants', True)

        if not self.static_path:
            self.path = '%s%s/' % (
                self.parent.path if self.parent else '/',
                self.slug,
            )

        if not self.position:
            self.position = 10 + (
                self.__class__._default_manager.filter(
                    parent_id=self.parent_id,
                ).order_by().aggregate(p=Max('position'))['p'] or 0
            )

        super(AbstractPage, self).save(*args, **kwargs)

        if save_descendants:
            for pk, node in self._branch_for_update().items():
                if pk == self.pk:
                    continue
                node.save(save_descendants=False)
    save.alters_data = True

    def get_absolute_url(self):
        """
        Return the page's absolute URL using ``reverse()``

        If path is ``/``, reverses ``pages:root`` without any arguments,
        alternatively reverses ``pages:page`` with an argument of ``path``.
        Note that this ``path`` is not the same as ``self.path`` -- slashes
        are stripped from the beginning and the end of the string to make
        building an URLconf more straightforward.
        """
        if self.path == '/':
            return reverse('pages:root')
        return reverse('pages:page', kwargs={
            'path': self.path.strip('/'),
        })
