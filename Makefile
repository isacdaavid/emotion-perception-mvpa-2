# author: Isaac David <isacdaavid@at@isacdaavid@dot@info>
# license: GPLv3 or later

SHELL := /bin/bash

BUILD_DIR := out
SRC_DIR := src
DATA_DIR := data

IDS_FILE := $(BUILD_DIR)/xnat/subject_metadata/fmri_subject_ids.csv
# note the use of the lazy assignment operator (strict evaluation) to avoid
# memoization of IDS after $(IDS_FILE) is regenerated
IDS = $(shell cut -d ' ' -f 1 $(IDS_FILE) | sort)
DICOMS = $(shell find $(DATA_DIR)/xnat/images/ -type d -name DICOM | \
                 grep -E '(fMRI_GazeCueing|FSPGR|T2)' | grep -v '00-PU' | sort)
VOLBRAIN_ZIPS = $(shell find $(DATA_DIR)/volbrain/ -type f -name '*.zip')
VOLBRAIN_IMAGES = $(shell find data/volbrain/ -type f -name 'native_n_*')
FMRI_NIFTIS = $(shell find $(DATA_DIR)/xnat/images/ -type f \
                      -name '*nifti.nii.gz' | grep GazeCueing | sort)

.PHONY : build all
all : build
build : eprime volbrain_tree volbrain_unzip feat-prepro

################################################################################
# FSL FEAT preprocessing
################################################################################

.PHONY : feat-prepro
#TODO: add prerequisite to _brain.nii.gz images, so as to complete dependency graphl
feat-prepro : $(addsuffix .feat,$(subst /resources/nifti.nii.gz,,$(subst xnat/images,feat,$(FMRI_NIFTIS))))
	@echo

$(DATA_DIR)/feat/%.feat : $(DATA_DIR)/xnat/images/%/resources/nifti.nii.gz $(SRC_DIR)/feat/design.fsf
	@t1dir=$(subst feat,volbrain,$@) ; \
	t1dir=$${t1dir%scans*} ; \
	t1=$$(find "$$t1dir" -name '*_brain.nii.gz') ; \
	featdir=$@ ; mkdir -p "$${featdir%/*}" ; \
	sed "s|MVPA_OUTPUTDIR|$$(pwd)/$@| ; s|MVPA_FEAT_FILES|$$(pwd)/$<| ; s|MVPA_HIGHRES_FILES|$$(pwd)/$$t1|" \
	     "$(SRC_DIR)/feat/design.fsf" > "$@.design.fsf" ; \
	feat "$@.design.fsf" ; sleep 5m

################################################################################
# volbrain-related rules
################################################################################

.PHONY : t1w_brain_extraction
t1w_brain_extraction : $(VOLBRAIN_IMAGES:.nii=_brain.nii.gz)
	@echo

$(DATA_DIR)/volbrain/%_brain.nii.gz : $(DATA_DIR)/volbrain/%.nii
	@echo 'extracting brain to $@'
	@fslmaths $< -mul "$(subst _n_,_mask_n_,$<)" "$@"

.PHONY : volbrain_unzip
volbrain_unzip : $(VOLBRAIN_ZIPS:.zip=.volbrain)
	@echo

# FIXME: requires manual upload/download from https://volbrain.upv.es
%.volbrain : %.zip
	@echo 'unzip-ing $<'
	@mkdir "$@" && unzip -q "$<" -d "$@" && rm "$<"

# create directory tree where to put volbrain's results
.PHONY : volbrain_tree
volbrain_tree : nifti $(addsuffix /, $(addprefix $(DATA_DIR)/volbrain/, $(IDS)))
	@echo

$(DATA_DIR)/volbrain/%/ :
	@echo 'creating directory at $@'
	@mkdir -p "$@"

################################################################################
# convert xnat DICOMs to Nifti
################################################################################

.PHONY : nifti
nifti : images $(DICOMS:DICOM=nifti.nii.gz)
	@echo

%nifti.nii.gz : %DICOM
	@echo 'building $@'
	@dcm2niix -f 'nifti' -g y -i y -t y -z y "$</.." > /dev/null

################################################################################
# xnat DICOMs: download, unzip. DO NOT PARALLELIZE (don't run with -j )
################################################################################

.PHONY : images
images : $(IDS_FILE)
	@mkdir -p "$(DATA_DIR)/xnat/$@"
	@targets=($$(cut -d ' ' -f 1 "$<" | sort)) ; \
	for i in $${targets[@]}; do \
	    [[ -d "$(DATA_DIR)/xnat/$@/$$i" ]] && continue ; \
	    if [[ ! -f "$(DATA_DIR)/xnat/$@/$${i}.zip" ]]; then \
	        printf 'downloading DICOMS for subject %d\n\n' "$$i" ; \
	        $(SRC_DIR)/xnat/$@/xnat-download.sh $$(grep "^$$i " $<) \
	                                            "$(DATA_DIR)/xnat/$@" || \
	            rm "$(DATA_DIR)/xnat/$@/$${i}.zip" ; \
	    fi ; \
	    printf "unzip-ing DICOMs for subject %d\n\n" "$$i" ; \
	    unzip -q "$(DATA_DIR)/xnat/$@/$${i}.zip" \
	          -d "$(DATA_DIR)/xnat/$@/" && rm "$(DATA_DIR)/xnat/$@/$${i}.zip" ; \
	done ;
# delete duplicate T1 directories. we'll do smoothing and normalisation manually
	@find "$(DATA_DIR)/xnat/$@/" -type d -name '*00-PU*' -prune \
	         -exec bash -c 'echo "deleting derived images" {} ; rm -r {}' \;
# delete fMRI sequences with missing volumes (<260)
	@rm -rf "$(DATA_DIR)/xnat/$@/517/scans/5-fMRI_GazeCueing_1"
	@rm -rf "$(DATA_DIR)/xnat/$@/812/scans/8-fMRI_GazeCueing_2" # TODO: rescue vols, unique sequence
	@echo

################################################################################
# eprime events: find files, clean and convert into design matrices
################################################################################

.PHONY : eprime
eprime : $(IDS_FILE) $(DATA_DIR)/$@/
	@printf 'building design matrices from eprime event lists\n\n'
# FIXME: is it feasible to write a generic per-file or per-subject rule?
	@rm -rf "$(BUILD_DIR)/$@"
# copy eprime event files into subject-specific directory structure
	@targets=($$(cut -d ' ' -f 1 "$<")) ; \
	for i in $${targets[@]}; do \
	    mkdir -p "$(BUILD_DIR)/$@/$${i}" ; \
	    find $(DATA_DIR)/$@/{victor,FEDERICA/TASK_Gaze\ Cueing} \
	         -type f -name "*$${i}*.txt" \
	         -exec cp {} "$(BUILD_DIR)/$@/$${i}" \; ; \
	done
# manually fix duplicates and repeats
	@rm $(BUILD_DIR)/$@/526/*RTs* \
	    $(BUILD_DIR)/$@/677/Gaze* \
	    "$(BUILD_DIR)/$@/682/Gaze Cueing_B Backup1 Backup1-682-1.txt" \
	    $(BUILD_DIR)/$@/678/{'Gaze Cueing_B Backup1-678-1.txt','Copia de Gaze Cueing_C Backup1-678-1.txt'} \
	    # $(BUILD_DIR)/$@/678/'Copia de Copia de Gaze Cueing_A Backup1 Backup1-678-1.txt'
	@mv $(BUILD_DIR)/$@/664/Copia\ de\ Gaze\ Cueing_{C,B}\ Backup1-664-1.txt
# homogenize disparate file names into {A,B,C}.txt
	@find $(BUILD_DIR)/$@ -type f -name '*.txt' -exec bash -c \
	    'mv "{}" $$(echo {} | sed -En "s/(.*)(\/)(.*)(_)(A|B|C)(.*)(.txt)/\1\2\5\7/p")' \;
# UTF-16 -> UTF-8, CRLF -> LF
	@find $(BUILD_DIR)/$@ -type f -name '*.txt' -exec bash -c \
	    'iconv -f UTF-16 -t UTF-8 "{}" | tr -d "\r" > "{}.new" && mv "{}.new" "{}"' \;
# more manual fixes (file contents)
	@sed -i 's/Sex: male/Sex: female/' $(BUILD_DIR)/$@/559/C.txt
	@sed -i 's/Age: 0/Age: 35/' $(BUILD_DIR)/$@/575/C.txt
	@sed -i 's/Age: 22/Age: 23/' $(BUILD_DIR)/$@/590/{B,C}.txt
	@sed -i 's/Age: 0/Age: 31/' $(BUILD_DIR)/$@/672/{A,B}.txt
	@sed -i 's/Sex: male/Sex: female/' $(BUILD_DIR)/$@/678/B.txt
	@sed -i 's/Age: 23/Age: 22/' $(BUILD_DIR)/$@/678/C.txt
	@sed -i 's/Age: 28/Age: 27/' $(BUILD_DIR)/$@/696/C.txt
# eprime event list -> pyMVPA sample attribute matrix
	@find $(BUILD_DIR)/$@ -type f -name '*.txt' -exec bash -c \
	    'awk -f "$(SRC_DIR)/eprime/eprime-to-csv.awk" -- "{}" > "{}.csv"' \;

################################################################################
# xnat's subject metadata DB: valid participants will be selected from there
################################################################################

$(IDS_FILE) : $(SRC_DIR)/xnat/subject_metadata/extract.R
	@printf '\building subject IDs list\n\n'
	@mkdir -p "$(BUILD_DIR)/xnat/subject_metadata"
	Rscript -e 'source("$(SRC_DIR)/xnat/subject_metadata/extract.R")'

