import datetime
import os
import statistics
from math import sqrt

import torch
import torch.nn as nn
from torch.optim import Adam, SGD
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter

from TrainingApp import TrainingApp
from dcan.loes_scoring.model.luna_model import LunaModel
from dcan.motion_qc.data_sets.mri_motion_qc_score_dataset import MRIMotionQcScoreDataset
from reprex.models import AlexNet3D_Dropout_Regression
from util.logconf import logging
from util.util import enumerateWithEstimate

log = logging.getLogger(__name__)
# log.setLevel(logging.WARN)
log.setLevel(logging.INFO)
# log.setLevel(logging.DEBUG)

# Used for computeBatchLoss and logMetrics to index into metrics_t/metrics_a
METRICS_LABEL_NDX = 0
METRICS_PRED_NDX = 1
METRICS_LOSS_NDX = 2
METRICS_SIZE = 3


class InfantMRIMotionQCTrainingApp(TrainingApp):
    def __init__(self, sys_argv=None):

        super().__init__()
        self.parser.add_argument('--tb-prefix',
                                 default='dcan',
                                 help="Data prefix to use for Tensorboard run. Defaults to dcan.",
                                 )

        self.parser.add_argument('--dset',
                                 help="Name of Dataset.",
                                 default='MRIMotionQcScoreDataset',
                                 )

        self.parser.add_argument('--qc_with_paths_csv',
                                 help="Location of data CSV file",
                                 default='data/eLabe/qc_img_paths.csv',
                                 )

        self.parser.add_argument('comment',
                                 help="Comment suffix for Tensorboard run.",
                                 nargs='?',
                                 default='loes_scoring',
                                 )

        self.parser.add_argument('--model',
                                 help="Model type.",
                                 default='AlexNet',
                                 )

        self.parser.add_argument('--optimizer',
                                 help="optimizer type.",
                                 default='Adam',
                                 )

        self.time_str = datetime.datetime.now().strftime('%Y-%m-%d_%H.%M.%S')
        # See https://pytorch.org/tutorials/beginner/saving_loading_models.html#saving-loading-model-for-inference
        self.parser.add_argument('--model-save-location',
                                 help="Where to save the trained model.",
                                 default=f'./elabe-model-{self.time_str}.pt',
                                 )

        self.cli_args = self.parser.parse_args(sys_argv)

        self.trn_writer = None
        self.val_writer = None
        self.totalTrainingSamples_count = 0
        self.global_step_tr = 0
        self.global_step_val = 0

        self.use_cuda = torch.cuda.is_available()
        self.device = torch.device("cuda" if self.use_cuda else "cpu")

        self.model = self.init_model(self.cli_args.model)
        self.optimizer = self.init_optimizer(self.cli_args.optimizer)

    def init_model(self, model_name):
        if model_name.lower() == 'luna':
            model = LunaModel()
        else:
            model = AlexNet3D_Dropout_Regression()
        if self.use_cuda:
            log.info("Using CUDA; {} devices.".format(torch.cuda.device_count()))
            if torch.cuda.device_count() > 1:
                model = nn.DataParallel(model)
            model = model.to(self.device)
        log.info("Model architecture {}".format(model))
        return model

    def init_optimizer(self, optimizer):
        if optimizer.lower() == 'sgd':
            return SGD(self.model.parameters(), lr=0.001, momentum=0.99)
        elif optimizer.lower() == 'adam':
            return Adam(self.model.parameters())
        assert False

    def init_train_dl(self):
        train_ds = MRIMotionQcScoreDataset(
            val_stride=10,
            is_val_set_bool=False)

        batch_size = self.cli_args.batch_size
        if self.use_cuda:
            batch_size *= torch.cuda.device_count()

        train_dl = DataLoader(
            train_ds,
            batch_size=batch_size,
            num_workers=self.cli_args.num_workers,
            pin_memory=self.use_cuda,
        )

        return train_dl

    def init_val_dl(self):
        val_ds = MRIMotionQcScoreDataset(
            val_stride=10,
            is_val_set_bool=True)

        batch_size = self.cli_args.batch_size
        if self.use_cuda:
            batch_size *= torch.cuda.device_count()

        val_dl = DataLoader(
            val_ds,
            batch_size=batch_size,
            num_workers=self.cli_args.num_workers,
            pin_memory=self.use_cuda,
        )

        return val_dl

    def init_tensorboard_writers(self):
        if self.trn_writer is None:
            log_dir = os.path.join('runs', self.cli_args.tb_prefix, self.time_str)

            self.trn_writer = SummaryWriter(
                log_dir=log_dir + '-trn_reg-' + self.cli_args.comment)
            self.val_writer = SummaryWriter(
                log_dir=log_dir + '-val_reg-' + self.cli_args.comment)

    def get_output_distributions(self):
        with torch.no_grad():
            val_dl = self.init_val_dl()
            self.model.eval()
            batch_iter = enumerateWithEstimate(
                val_dl,
                "get_output_distributions",
                start_ndx=val_dl.num_workers,
            )
            distributions = {1: [], 2: [], 3: [], 4: [], 5: []}
            for batch_ndx, batch_tup in batch_iter:
                input_t, label_t = batch_tup
                x = input_t.to(self.device, non_blocking=True)
                labels = label_t.to(self.device, non_blocking=True)
                outputs = self.model(x)
                actual = self.get_actual(outputs).tolist()
                n = len(labels)
                for i in range(n):
                    label_int = int(labels[i].item())
                    distributions[label_int].append(actual[i])
            for distribution in distributions:
                distributions[distribution] = sorted(distributions[distribution])

        return distributions

    def get_standardized_rmse(self):
        with torch.no_grad():
            val_dl = self.init_val_dl()
            self.model.eval()
            batch_iter = enumerateWithEstimate(
                val_dl,
                "get_standardized_rmse",
                start_ndx=val_dl.num_workers,
            )
            squares_list = []
            prediction_list = []
            for batch_ndx, batch_tup in batch_iter:
                input_t, label_t = batch_tup
                x = input_t.to(self.device, non_blocking=True)
                labels = label_t.to(self.device, non_blocking=True)
                outputs = self.model(x)
                actual = self.get_actual(outputs)
                prediction_list.extend(actual.tolist())
                difference = torch.subtract(labels, actual)
                squares = torch.square(difference)
                squares_list.extend(squares.tolist())
            rmse = sqrt(sum(squares_list) / len(squares_list))
            sigma = statistics.stdev(prediction_list)

            return rmse / sigma

    def main(self):
        log.info("Starting {}, {}".format(type(self).__name__, self.cli_args))

        train_dl = self.init_train_dl()
        val_dl = self.init_val_dl()

        for epoch_ndx in range(1, self.cli_args.epochs + 1):
            log.info("Epoch {} of {}, {}/{} batches of size {}*{}".format(
                epoch_ndx,
                self.cli_args.epochs,
                len(train_dl),
                len(val_dl),
                self.cli_args.batch_size,
                (torch.cuda.device_count() if self.use_cuda else 1),
            ))

            trn_metrics_t = self.do_training(epoch_ndx, train_dl)
            self.log_metrics(epoch_ndx, 'trn', trn_metrics_t)

            val_metrics_t = self.do_validation(epoch_ndx, val_dl)
            self.log_metrics(epoch_ndx, 'val', val_metrics_t)

        if hasattr(self, 'trn_writer'):
            self.trn_writer.close()
            self.val_writer.close()

        try:
            standardized_rmse = self.get_standardized_rmse()
            log.info(f'standardized_rmse: {standardized_rmse}')
        except ZeroDivisionError as err:
            print('Could not compute stanardized RMSE because sigma is 0:', err)

        output_distributions = self.get_output_distributions()
        log.info(f'output_distributions: {output_distributions}')

        torch.save(self.model.state_dict(), self.cli_args.model_save_location)

    def do_training(self, epoch_ndx, train_dl):
        self.init_tensorboard_writers()
        self.model.train()
        trn_metrics_g = torch.zeros(
            METRICS_SIZE,
            len(train_dl.dataset),
            device=self.device,
        )

        batch_iter = enumerateWithEstimate(
            train_dl,
            "E{} Training".format(epoch_ndx),
            start_ndx=train_dl.num_workers,
        )
        for batch_ndx, batch_tup in batch_iter:
            self.optimizer.zero_grad()

            loss = self.compute_batch_loss(
                batch_ndx,
                batch_tup,
                train_dl.batch_size,
                trn_metrics_g, True
            )

            loss.backward()
            self.optimizer.step()

            # # This is for adding the model graph to TensorBoard.
            # if epoch_ndx == 1 and batch_ndx == 0:
            #     with torch.no_grad():
            #         model = LunaModel()
            #         self.trn_writer.add_graph(model, batch_tup[0], verbose=True)
            #         self.trn_writer.close()

        self.totalTrainingSamples_count += len(train_dl.dataset)

        return trn_metrics_g.to('cpu')

    def do_validation(self, epoch_ndx, val_dl):
        with torch.no_grad():
            self.model.eval()
            val_metrics_g = torch.zeros(
                METRICS_SIZE,
                len(val_dl.dataset),
                device=self.device,
            )

            batch_iter = enumerateWithEstimate(
                val_dl,
                "E{} Validation ".format(epoch_ndx),
                start_ndx=val_dl.num_workers,
            )
            for batch_ndx, batch_tup in batch_iter:
                self.compute_batch_loss(
                    batch_ndx, batch_tup, val_dl.batch_size, val_metrics_g, False)

        return val_metrics_g.to('cpu')

    def compute_batch_loss(self, batch_ndx, batch_tup, batch_size, metrics_g, is_training):
        input_t, label_t = batch_tup

        x = input_t.to(self.device, non_blocking=True)
        labels = label_t.to(self.device, non_blocking=True)

        outputs = self.model(x)

        criterion = nn.MSELoss()
        actual = self.get_actual(outputs)
        log.debug(f'actual: {actual}')
        log.debug(f'labels: {labels}')
        loss = criterion(actual, labels)
        if is_training:
            self.trn_writer.add_scalar("Loss/train", loss, self.global_step_tr)
            self.global_step_tr += 1
        else:
            self.val_writer.add_scalar("Loss/validation", loss, self.global_step_val)
            self.global_step_val += 1

        start_ndx = batch_ndx * batch_size
        end_ndx = start_ndx + label_t.size(0)

        metrics_g[METRICS_LABEL_NDX, start_ndx:end_ndx] = \
            labels[0]
        # metrics_g[METRICS_PRED_NDX, start_ndx:end_ndx] = \
        #     outputs[0]
        metrics_g[METRICS_LOSS_NDX, start_ndx:end_ndx] = \
            loss

        return loss.mean()

    def log_metrics(
            self,
            epoch_ndx,
            mode_str,
            metrics_t,
    ):
        log.info("E{} {}".format(
            epoch_ndx,
            type(self).__name__,
        ))

        metrics_dict = {'loss/all': metrics_t[METRICS_LOSS_NDX].mean()}

        log.info(
            ("E{} {:8} {loss/all:.4f} loss, "
             ).format(
                epoch_ndx,
                mode_str,
                **metrics_dict,
            )
        )

        writer = getattr(self, mode_str + '_writer')

        for key, value in metrics_dict.items():
            writer.add_scalar(key, value, self.totalTrainingSamples_count)

        # score = 1 \
        #     + metrics_dict['pr/f1_score'] \
        #     - metrics_dict['loss/mal'] * 0.01 \
        #     - metrics_dict['loss/all'] * 0.0001
        #
        # return score

    # def logModelMetrics(self, model):
    #     writer = getattr(self, 'trn_writer')
    #
    #     model = getattr(model, 'module', model)
    #
    #     for name, param in model.named_parameters():
    #         if param.requires_grad:
    #             min_data = float(param.ddo_validationata.min())
    #             max_data = float(param.data.max())
    #             max_extent = max(abs(min_data), abs(max_data))
    #
    #             # bins = [x/50*max_extent for x in range(-50, 51)]
    #
    #             try:
    #                 writer.add_histogram(
    #                     name.rsplit('.', 1)[-1] + '/' + name,
    #                     param.data.cpu().numpy(),
    #                     # metrics_a[METRICS_PRED_NDX, negHist_mask],
    #                     self.totalTrainingSamples_count,
    #                     # bins=bins,
    #                 )
    #             except Exception as e:
    #                 log.error([min_data, max_data])
    #                 raise


if __name__ == '__main__':
    InfantMRIMotionQCTrainingApp().main()