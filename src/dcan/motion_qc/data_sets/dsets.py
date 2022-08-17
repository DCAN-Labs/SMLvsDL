import csv
import functools
import glob
import logging
import os
from collections import namedtuple
from operator import attrgetter

from util.disk import getCache

import nibabel as nib
import numpy as np
import torchio as tio

log = logging.getLogger(__name__)
# log.setLevel(logging.WARN)
# log.setLevel(logging.INFO)
log.setLevel(logging.DEBUG)

raw_cache = getCache('dcan_raw')

CandidateInfoTuple = namedtuple(
    'CandidateInfoTuple',
    'smriPath_str, rating_int'
)


@functools.lru_cache(1)
def get_candidate_info_list():
    # We construct a set with all series_uids that are present on disk.
    # This will let us use the data, even if we haven't downloaded all of
    # the subsets yet.
    qc_with_paths_csv = 'data/eLabe/qc_img_paths.csv'
    with open(qc_with_paths_csv, "r") as f:
        candidate_info_list = []
        for row in list(csv.reader(f))[1:]:
            smri_path_str = row[0]
            motion_q_cscore_int = float(row[1])

            candidate_info_list.append(CandidateInfoTuple(
                smri_path_str,
                motion_q_cscore_int,
            ))

    candidate_info_list.sort(reverse=True, key=attrgetter('rating_int'))

    return candidate_info_list


class Mri:
    def __init__(self, smri_path):
        mri_path = smri_path

        mprage_image = tio.ScalarImage(mri_path)
        crop_or_pad = tio.CropOrPad(
            (208, 300, 320),
        )
        transform = tio.Compose([
            crop_or_pad,
            tio.ToCanonical(),
            tio.ZNormalization(masking_method=tio.ZNormalization.mean),
        ])
        transformed_mprage_image = transform(mprage_image)
        mprage_image_tensor = transformed_mprage_image.data
        mprage_image_tensor = mprage_image_tensor.squeeze()
        mri_a = np.array(mprage_image_tensor, dtype=np.float32)

        self.hu_a = mri_a

    def get_raw_candidate(self):
        mri_chunk = self.hu_a

        return mri_chunk


@functools.lru_cache(1, typed=True)
def get_mri(series_uid):
    return Mri(series_uid)


@raw_cache.memoize(typed=True)
def get_mri_raw_candidate(series_uid):
    mri = get_mri(series_uid)
    mri_chunk = mri.get_raw_candidate()
    return mri_chunk