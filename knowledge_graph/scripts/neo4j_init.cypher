// Neo4j initialization script for the HIV/AIDS consultation search graph.
// This script is designed for Neo4j 5.x.
// It creates constraints and indexes for the current search-only ontology.

// ---------------------------------------------------------------------------
// Search graph node uniqueness constraints
// ---------------------------------------------------------------------------

CREATE CONSTRAINT disease_id IF NOT EXISTS
FOR (n:Disease)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT disease_phase_id IF NOT EXISTS
FOR (n:DiseasePhase)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT opportunistic_infection_id IF NOT EXISTS
FOR (n:OpportunisticInfection)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT comorbidity_id IF NOT EXISTS
FOR (n:Comorbidity)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT syndrome_or_complication_id IF NOT EXISTS
FOR (n:SyndromeOrComplication)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT tumor_id IF NOT EXISTS
FOR (n:Tumor)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT pathogen_id IF NOT EXISTS
FOR (n:Pathogen)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT symptom_id IF NOT EXISTS
FOR (n:Symptom)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT sign_id IF NOT EXISTS
FOR (n:Sign)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT clinical_attribute_id IF NOT EXISTS
FOR (n:ClinicalAttribute)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT lab_test_id IF NOT EXISTS
FOR (n:LabTest)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT lab_finding_id IF NOT EXISTS
FOR (n:LabFinding)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT imaging_finding_id IF NOT EXISTS
FOR (n:ImagingFinding)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT risk_factor_id IF NOT EXISTS
FOR (n:RiskFactor)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT risk_behavior_id IF NOT EXISTS
FOR (n:RiskBehavior)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT population_group_id IF NOT EXISTS
FOR (n:PopulationGroup)
REQUIRE n.id IS UNIQUE;

// ---------------------------------------------------------------------------
// Name and canonical-name indexes for retrieval / entity linking
// ---------------------------------------------------------------------------

CREATE INDEX disease_name IF NOT EXISTS
FOR (n:Disease)
ON (n.name);

CREATE INDEX disease_canonical_name IF NOT EXISTS
FOR (n:Disease)
ON (n.canonical_name);

CREATE INDEX disease_phase_name IF NOT EXISTS
FOR (n:DiseasePhase)
ON (n.name);

CREATE INDEX opportunistic_infection_name IF NOT EXISTS
FOR (n:OpportunisticInfection)
ON (n.name);

CREATE INDEX opportunistic_infection_canonical_name IF NOT EXISTS
FOR (n:OpportunisticInfection)
ON (n.canonical_name);

CREATE INDEX comorbidity_name IF NOT EXISTS
FOR (n:Comorbidity)
ON (n.name);

CREATE INDEX syndrome_or_complication_name IF NOT EXISTS
FOR (n:SyndromeOrComplication)
ON (n.name);

CREATE INDEX tumor_name IF NOT EXISTS
FOR (n:Tumor)
ON (n.name);

CREATE INDEX pathogen_name IF NOT EXISTS
FOR (n:Pathogen)
ON (n.name);

CREATE INDEX pathogen_canonical_name IF NOT EXISTS
FOR (n:Pathogen)
ON (n.canonical_name);

CREATE INDEX symptom_name IF NOT EXISTS
FOR (n:Symptom)
ON (n.name);

CREATE INDEX symptom_canonical_name IF NOT EXISTS
FOR (n:Symptom)
ON (n.canonical_name);

CREATE INDEX sign_name IF NOT EXISTS
FOR (n:Sign)
ON (n.name);

CREATE INDEX sign_canonical_name IF NOT EXISTS
FOR (n:Sign)
ON (n.canonical_name);

CREATE INDEX clinical_attribute_name IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.name);

CREATE INDEX clinical_attribute_canonical_name IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.canonical_name);

CREATE INDEX lab_test_name IF NOT EXISTS
FOR (n:LabTest)
ON (n.name);

CREATE INDEX lab_test_canonical_name IF NOT EXISTS
FOR (n:LabTest)
ON (n.canonical_name);

CREATE INDEX lab_finding_name IF NOT EXISTS
FOR (n:LabFinding)
ON (n.name);

CREATE INDEX lab_finding_canonical_name IF NOT EXISTS
FOR (n:LabFinding)
ON (n.canonical_name);

CREATE INDEX imaging_finding_name IF NOT EXISTS
FOR (n:ImagingFinding)
ON (n.name);

CREATE INDEX imaging_finding_canonical_name IF NOT EXISTS
FOR (n:ImagingFinding)
ON (n.canonical_name);

CREATE INDEX risk_factor_name IF NOT EXISTS
FOR (n:RiskFactor)
ON (n.name);

CREATE INDEX risk_factor_canonical_name IF NOT EXISTS
FOR (n:RiskFactor)
ON (n.canonical_name);

CREATE INDEX risk_behavior_name IF NOT EXISTS
FOR (n:RiskBehavior)
ON (n.name);

CREATE INDEX risk_behavior_canonical_name IF NOT EXISTS
FOR (n:RiskBehavior)
ON (n.canonical_name);

CREATE INDEX population_group_name IF NOT EXISTS
FOR (n:PopulationGroup)
ON (n.name);

CREATE INDEX population_group_canonical_name IF NOT EXISTS
FOR (n:PopulationGroup)
ON (n.canonical_name);

// ---------------------------------------------------------------------------
// Evidence acquisition metadata indexes reserved for future A3 ranking
// ---------------------------------------------------------------------------

CREATE INDEX symptom_acquisition_mode IF NOT EXISTS
FOR (n:Symptom)
ON (n.acquisition_mode);

CREATE INDEX symptom_evidence_cost IF NOT EXISTS
FOR (n:Symptom)
ON (n.evidence_cost);

CREATE INDEX clinical_attribute_acquisition_mode IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.acquisition_mode);

CREATE INDEX clinical_attribute_evidence_cost IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.evidence_cost);

CREATE INDEX lab_finding_acquisition_mode IF NOT EXISTS
FOR (n:LabFinding)
ON (n.acquisition_mode);

CREATE INDEX lab_finding_evidence_cost IF NOT EXISTS
FOR (n:LabFinding)
ON (n.evidence_cost);

CREATE INDEX imaging_finding_acquisition_mode IF NOT EXISTS
FOR (n:ImagingFinding)
ON (n.acquisition_mode);

CREATE INDEX imaging_finding_evidence_cost IF NOT EXISTS
FOR (n:ImagingFinding)
ON (n.evidence_cost);

CREATE INDEX risk_behavior_acquisition_mode IF NOT EXISTS
FOR (n:RiskBehavior)
ON (n.acquisition_mode);

CREATE INDEX risk_behavior_evidence_cost IF NOT EXISTS
FOR (n:RiskBehavior)
ON (n.evidence_cost);

CREATE INDEX lab_finding_semantic IF NOT EXISTS
FOR (n:LabFinding)
ON (n.test_id, n.operator, n.value, n.unit);
