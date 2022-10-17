#!/bin/bash -l
#SBATCH --job-name=alex-net.training.agate
#SBATCH --time=24:00:00
#SBATCH --partition=a100-4
#SBATCH --mem-per-cpu=64G
#SBATCH --gres=gpu:a100:1
#SBATCH --output=alex_net.training.agate-%j.out
#SBATCH --error=alex_net.training.agate-%j.err

pwd; hostname; date
echo jobid=${SLURM_JOB_ID}; echo nodelist=${SLURM_JOB_NODELIST}

module load python3/3.8.3_anaconda2020.07_mamba
__conda_setup="$(`which conda` 'shell.bash' 'hook' 2> /dev/null)"
eval "$__conda_setup"

echo CUDA_VISIBLE_DEVICES: $CUDA_VISIBLE_DEVICES

cd /home/miran045/reine097/projects/AlexNet_Abrol2021 || exit
export PYTHONPATH=PYTHONPATH:"/home/miran045/reine097/projects/AlexNet_Abrol2021/src":"/home/miran045/reine097/projects/AlexNet_Abrol2021/reprex"
/home/miran045/reine097/projects/AlexNet_Abrol2021/venv/bin/python \
  /home/miran045/reine097/projects/AlexNet_Abrol2021/src/dcan/motion_qc/training/training.py --num-workers=1 --batch-size=8 \
  --qc_with_paths_csv="/panfs/jay/groups/6/faird/shared/projects/motion-QC-generalization/code/bcp_and_elabe_qc_train_space-infant_unique.csv" \
  --tb-prefix="bcp_and_elabe_qc_train_space-infant_uniqueScore" --epochs=1000 --model="AlexNet" --dset="MRIMotionQcScoreDataset" \
  --model-save-location="/home/feczk001/shared/data/AlexNet/motion-qc-model04.pt" --optimizer="Adam" "motion_qc_AlexNet_model_04"
