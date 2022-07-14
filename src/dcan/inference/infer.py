import sys

import torch
import nibabel as nib
import numpy as np

from reprex.models import AlexNet3D_Dropout_Regression
from util.logconf import logging

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


def infer_bcp_motion_qc_score(model_weights_file, mri_path):
    model = AlexNet3D_Dropout_Regression()
    model.load_state_dict(torch.load(model_weights_file,
                                     map_location='cpu'))
    model.eval()
    with torch.no_grad():
        mri_nii_gz = nib.load(mri_path)
        mri_a = np.array(mri_nii_gz.get_fdata().copy(), dtype=np.float32)
        candidate_t = torch.from_numpy(mri_a).to(torch.float32)
        candidate_t = torch.unsqueeze(candidate_t.unsqueeze(0), dim=0)
        prediction_t_a = model(candidate_t)
        prediction = prediction_t_a[0].item()

        return prediction


if __name__ == "__main__":
    model_weights_fl = sys.argv[1]
    mri_file = sys.argv[2]
    pred = infer_bcp_motion_qc_score(model_weights_fl, mri_file)
    log.info(f'prediction: {pred}')