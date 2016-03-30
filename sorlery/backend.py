from sorl.thumbnail.conf import settings, defaults as default_settings
from sorl.thumbnail.helpers import tokey, serialize
from sorl.thumbnail.images import DummyImageFile, ImageFile
from sorl.thumbnail.kvstores.base import add_prefix
from sorl.thumbnail.helpers import serialize, deserialize
from sorl.thumbnail import default
from sorl.thumbnail.parsers import parse_geometry
from sorl.thumbnail.base import ThumbnailBackend

from sorlery.tasks import create_thumbnail


class QueuedThumbnailBackend(ThumbnailBackend):
    """
    Queue thumbnail generation with django-celery.
    """

    def _get_thumbnail_filename(self, source, geometry_string, options):
        """
        Computes the destination filename.
        """

        split_path = re.sub(r'^%s%s?' % (source.storage.path(
            ''), os.sep), '', source.name).split(os.sep)
        split_path.insert(-1, geometry_string)

        # attempt to slugify the filename to make it SEO-friendly
        split_name = split_path[-1].split('.')
        try:
            split_path[-1] = '%s.%s' % (
                slugify('.'.join(split_name[:-1])), split_name[-1])
        except:
            # on fail keep the original filename
            pass

        path = os.sep.join(split_path)

        # if the path already starts with THUMBNAIL_PREFIX do not concatenate the PREFIX
        # this way we avoid ending up with a url like
        # /images/images/120x120/my.png
        if not path.startswith(settings.THUMBNAIL_PREFIX):
            return '%s%s' % (settings.THUMBNAIL_PREFIX, path)

        return path

    def get_thumbnail(self, file_, geometry_string, **options):
        source = ImageFile(file_)

        for key, value in self.default_options.iteritems():
            options.setdefault(key, value)

        for key, attr in self.extra_options:
            value = getattr(settings, attr)
            if value != getattr(default_settings, attr):
                options.setdefault(key, value)

        # Generate a name for the thumbnail
        name = self._get_thumbnail_filename(source, geometry_string, options)

        # See if we've got a hit in the cache
        thumbnail = ImageFile(name, default.storage)
        cached = default.kvstore.get(thumbnail)
        if cached:
            return cached

        # We cannot check if the file exists, as remote storage is slow. If
        # we have reached this point, the image does not exist in our kvstore
        # so create the entry and queue the generation of the image.
        #
        # Note: If the thumbnail file has been deleted, you will need to manually
        # clear the corresponding row from the kvstore to have thumbnail rebuilt.
        job = create_thumbnail.delay(file_, geometry_string, options, name)
        if job:
            geometry = parse_geometry(geometry_string)
            # We can't add a source row to the kvstore without the size
            # information being looked up, so add dummy information here
            # We'll need to correct this information when we generate the thumbnail
            source.set_size(geometry)
            default.kvstore.get_or_set(source)

            # We don't want to do any file access in this thread, so we tell sorlery
            # to proceed as normal and cheekily update the name and storage after
            # the hash has been calculated.
            thumbnail.set_size(geometry)
            default.kvstore.set(thumbnail, source)

            # Now we go back and manually update the thumbnail to point at the source image
            # Hopefully someone can suggest a better way to do this ... but the sorl internals
            # don't make it easy to.
            rawvalue = default.kvstore._get_raw(add_prefix(thumbnail.key))
            rawvaluearr = deserialize(rawvalue)
            rawvaluearr['name'] = file_.name
            default.kvstore._set_raw(add_prefix(thumbnail.key), serialize(rawvaluearr))

        thumbnail.name = file_.name
        return thumbnail
