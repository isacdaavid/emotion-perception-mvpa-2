#!/usr/bin/python2

# author: Isaac David <isacdaavid@at@isacdaavid@dot@info>
# license: GPLv3 or later

import matplotlib
matplotlib.use('Agg') # Force matplotlib to not use any Xwindows backend
import matplotlib.pyplot as plt
from mvpa2.tutorial_suite import *
import sys

# arguments passed to script
ATTR_FNAME = sys.argv[1]
BOLD_FNAME = sys.argv[2]
MASK_FNAME = sys.argv[3]
OUTDIR = sys.argv[4]

STEP = 200 # time step between different HRF delays. ms
TIME_START = 0 # first HRF delay to test for. ms
TIME_LIMIT = 20000 # maximum HRF delay to test for. ms
MAX_SAMPLES = 16 # samples per category = n-fold / 3
PERMUTATIONS = 2 # label permutations used to estimate null accuracy distrib
ANOVA_SELECTION = .01 # selected voxels proportion

################################################################################
# volume labeling
################################################################################

SLICE_TIMING_REFERENCE = +1000 # ms

def label(ds, attr, slice_timing_reference, hrf_delay):
        # time-shift for eprime preambulus
        onset_times = np.array(attr.onset_time) - attr.onset_time[0]
        attr.pop('onset_time')
        attr['onset_time'] = np.ndarray.tolist(onset_times)

        # convert volume times to ms, as eprime, then time-slice-shift
        # (tr=2s, interleaved)
        ds.sa.time_coords = (ds.sa.time_coords * 1000) + slice_timing_reference

        indices = list()
        # exclude volumes prior to 1 hrf_delay, since they are not supposed to
        # to reflect a maximum of task-related HRF activity
        for vol_time in ds[ds.sa.time_coords >= hrf_delay].sa.time_coords:
                # select latest event to occur before (vol_time - hrf_delay)
                match_floor = \
                        max(onset_times[onset_times <= (vol_time - hrf_delay)])
                indices.append(attr.onset_time.index(match_floor))

        # although we won't return them, excluded premature volumes still are
        # assigned the attributes of the first non-excluded one
        useless_vols = 0
        while(len(indices) < len(ds)):
                indices.insert(0, indices[0])
                useless_vols = useless_vols + 1

        # label volumes according to attribute sublist
        for at in attr.keys():
                ds.sa[at] = [attr[at][index] for index in indices]

        return ds[useless_vols:]

################################################################################
# classification
################################################################################

def subsample(ds0):
        ds1 = ds0[{'emotion': ['happy', 'neutral', 'sad']}]
        # sample from independent blocks, so as to avoid temporally-correlated
        # volumes in both training and test partitions. take earliest volume
        indep = []
        for b in np.unique(ds1.sa.block):
                indep.append(min(ds1[{'block': [b]}].sa.time_indices))
        ds2 = ds1[{'time_indices': indep}]

        # good machines are emotionally-balanced machines ;)
        max_samples = min(np.unique(ds2[{'emotion': ['happy',
                                                     'sad',
                                                     'neutral']}].sa.emotion,
                                    return_counts = True)[1])

        if max_samples > MAX_SAMPLES:
                max_samples = MAX_SAMPLES
        ds3 = ds2[{'emotion': ['happy']}][:max_samples:1]
        ds3 = vstack((ds3, ds2[{'emotion': ['sad']}][:max_samples:1]))
        ds3 = vstack((ds3, ds2[{'emotion': ['neutral']}][:max_samples:1]))

        ds3.sa['targets'] = ds3.sa.emotion # this is the label/target attribute
        return ds3

def train():
        clf = LinearCSVMC()
	fsel = SensitivityBasedFeatureSelection(
	       		OneWayAnova(),
                        FractionTailSelector(ANOVA_SELECTION,
					     mode = 'select',
                                             tail = 'upper'))
	fclf = FeatureSelectionClassifier(clf, fsel)
        cv = CrossValidation(fclf, NFoldPartitioner(attr = 'block'),
                             errorfx = lambda p, t: np.mean(p == t),
                             enable_ca=['stats'])
        return fclf,cv

################################################################################
# Monte-Carlo null hypothesis estimation
################################################################################

def null_cv(permutations = PERMUTATIONS):
        repeater = Repeater(count = permutations)
        permutator = AttributePermutator('targets',
                                         limit={'partitions': 1}, count = 1)
        clf = LinearCSVMC()
	fsel = SensitivityBasedFeatureSelection(
	       		OneWayAnova(),
                        FractionTailSelector(ANOVA_SELECTION,
					     mode = 'select',
                                             tail = 'upper'))
	fclf = FeatureSelectionClassifier(clf, fsel)
        partitioner = NFoldPartitioner(attr = 'block')
        cv = CrossValidation(fclf,
                             ChainNode([partitioner, permutator],
                                       space = partitioner.get_space()),
                             errorfx = lambda p, t: np.mean(p == t),
                             postproc = mean_sample())
        distr_est = MCNullDist(repeater, tail = 'right',
                               measure = cv,
                               enable_ca = ['dist_samples'])
        cv_mc = CrossValidation(fclf,
                                partitioner,
                                errorfx = lambda p, t: np.mean(p == t),
                                postproc = mean_sample(),
                                null_dist = distr_est,
                                enable_ca = ['stats'])
        return fclf,cv_mc

def make_null_dist_plot(dist_samples, empirical):
     pl.hist(dist_samples, bins = 100, normed = True, alpha = 0.8)
     pl.axvline(empirical, color='red')
     # a priori chance-level
     pl.axvline(0.333, color='black', ls='--')
     # scale x-axis to full range of possible error values
     pl.xlim(0,1)
     pl.xlabel('Average cross-validated classification error')

################################################################################
# sensitivity analysis
################################################################################

# - remove sign (there's no interpretation to feature importance direction in
#   orthogonal vector to SVM hyperplane, other than encoding class)
# - L2-normalize to make sure vector sum is meaningful
# - sum
# - rescale to maximum weight (summed masks will be comparable operators)
# - optionally, return n most significant weights
def normalize_weights(weight_lists, significance = 1):
        for i in range(0, len(weight_lists)):
                weight_lists[i] = l2_normed(abs(weight_lists[i]))
        if len(weight_lists) > 1:
                total = np.sum(weight_lists, axis = 0)
        else:
                total = weight_lists[0]
        total /= max(total)
        ntile = np.sort(total)[-int(round(len(total) * significance))]
        return np.array([(0 if (x < ntile) else x) for x in total])

def sensibility_maps_aux(model, ds):
        analyzer = model.get_sensitivity_analyzer()
        return analyzer(ds)

# outputs the computed "activation" maps (rather, sensitivity masks)
def sensibility_maps(model, ds):
        sens = sensibility_maps_aux(model, ds)
        for i in range(0, len(sens.targets)):
                sensmap = sens.targets[i]
                # emotional vs neutral
                if str(sensmap) == "('neutral', 'happy')" or \
                   str(sensmap) == "('happy', 'neutral')":
                   i1_1 = i
                if str(sensmap) == "('neutral', 'sad')" or \
                   str(sensmap) == "('sad', 'neutral')":
                   i1_2 = i
                # happy vs sad
                if str(sensmap) == "('happy', 'sad')" or \
                   str(sensmap) == "('sad', 'happy')":
                   i2 = i

        all_weights = normalize_weights(np.array([sens[0].samples[0],
                                                  sens[1].samples[0],
                                                  sens[2].samples[0]]))
        emo_vs_neu = normalize_weights(np.array([sens[i1_1].samples[0],
                                                 sens[i1_2].samples[0]]))
        hap_vs_sad = normalize_weights(np.array([sens[i2].samples[0]]))

        return all_weights,emo_vs_neu,hap_vs_sad

# percentage of voxels with non-zero weights
def non_empty_weights_proportion(weights):
        return len(weights[weights != 0.0]) / float(len(weights))

################################################################################
# main
################################################################################

# load eprime events/design matrix (aka target attributes)
attr = SampleAttributes(ATTR_FNAME,
                        header = ['onset_time', 'age', 'sex', 'handedness',
                                  'block', 'visual', 'face', 'face_gender',
                                  'emotion', 'gaze', 'target', 'response'])

result_dist = []
fo = open(OUTDIR + "/result-time-series.txt", "w+")
for delay in range(TIME_START, TIME_LIMIT, STEP):
        ds = fmri_dataset(BOLD_FNAME, mask = MASK_FNAME)
        ds3 = label(ds, attr, SLICE_TIMING_REFERENCE, delay)
        ds4 = subsample(ds3)
        model,validator = train()
        results = validator(ds4)
        result_dist.append(np.mean(results))
        sens = sensibility_maps_aux(model, ds4)
        weights = sens[0].samples[0] + sens[1].samples[0] + sens[2].samples[0]
        print(np.mean(results))
        fo.writelines(str(ds4.nsamples / 3) + " " + str(np.mean(results)) + " "
                      + str(non_empty_weights_proportion(weights)) + "\n")

fo.close()

plt.plot(result_dist)
plt.savefig(OUTDIR + '/result-time-series.svg')
plt.close()
plt.hist(result_dist, bins = 1000)
plt.savefig(OUTDIR + '/result-dist.svg')
plt.close()

# best model ###################################################################

optimal_delay = (result_dist.index(max(result_dist)) * STEP) + TIME_START
ds = fmri_dataset(BOLD_FNAME, mask = MASK_FNAME)
ds3 = label(ds, attr, SLICE_TIMING_REFERENCE, optimal_delay)
ds4 = subsample(ds3)
# null accuracy estimation using Monte-Carlo method
model,validator = null_cv(PERMUTATIONS)
results = validator(ds4)

fo = open(OUTDIR + "/conf-matrix.txt", "w+")
fo.writelines(validator.ca.stats.as_string(description = True))
fo.close()

validator.ca.stats.plot()
plt.savefig(OUTDIR + '/conf-matrix.svg')
plt.close()

fo = open(OUTDIR + '/null-dist.txt', "w+")
fo.writelines("\n".join(str(i) \
	for i in validator.null_dist.ca.dist_samples.samples.tolist()[0][0]))
fo.close()

make_null_dist_plot(np.ravel(validator.null_dist.ca.dist_samples),
                    np.mean(results))
plt.savefig(OUTDIR + '/null-dist.svg')
plt.close()

# sensibility maps #############################################################

all_weights,emo_vs_neu,hap_vs_sad = sensibility_maps(model, ds4)

# distribution of non-zero weights, normalized to the maximum one
plt.hist(all_weights[all_weights != 0] / max(all_weights), bins=50)
plt.savefig(OUTDIR + '/weights-dist.svg')
plt.close()

# export sensitivity maps
nimg = map2nifti(ds, all_weights) # use ds.a.mapper to reverse flattening
nimg.to_filename(OUTDIR + '/all-weights-nn.nii.gz')
nimg = map2nifti(ds, emo_vs_neu)
nimg.to_filename(OUTDIR + '/emo-vs-neu-weights-nn.nii.gz')
nimg = map2nifti(ds, hap_vs_sad)
nimg.to_filename(OUTDIR + '/hap_vs_sad-weights-nn.nii.gz')

