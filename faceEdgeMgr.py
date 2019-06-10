# -*- coding: utf-8 -*-
"""
Created on Sun Apr 21 19:51:42 2019

@author: Peter Ludikar
"""

import logging
from pprint import pformat

from sys import getrefcount as grc

from collections import defaultdict

import adsk.core, adsk.fusion
import traceback
import weakref
import json
from functools import reduce, lru_cache

from . import dbutils as dbUtils
from math import sqrt, pi

#constants - to keep attribute group and names consistent
DBGROUP = 'dbGroup'
DBEDGE_REGISTERED = 'dbEdgeRegistered'
DBFACE_REGISTERED = 'dbFaceRegistered'
DBEDGE_SELECTED = 'dbEdgeSelected'
DBFACE_SELECTED = 'dbFaceSelected'
FACE_ID = 'faceID'
REV_ID = 'revId'
ID = 'id'
DEBUGLEVEL = logging.NOTSET



faceSelections = lambda selectionObjects: list(filter(lambda face: face.objectType == adsk.fusion.BRepFace.classType(), selectionObjects))
edgeSelections = lambda selectionObjects: list(filter(lambda edge: edge.objectType == adsk.fusion.BRepEdge.classType(), selectionObjects))

# Generate occurrence hash
calcOccHash = lambda x: x.assemblyContext.name if x.assemblyContext else x.body.name

# Generate an edgeHash or faceHash from object
calcHash = lambda x: str(x.tempId) + ':' + x.assemblyContext.name.split(':')[-1] if x.assemblyContext else str(x.tempId) + ':' + x.body.name


class FaceEdgeMgr:


    def __init__(self):
        self.logger = logging.getLogger('dogbone.mgr')
        
        self.logger.info('---------------------------------{}---------------------------'.format('faceEdgeMgr'))
        self.logger.debug('faceEdgeMgr initiated')
        self.registeredFaces = {} #key: occurrenceHash value: dict of key: facehash value: faceObjects
        self.registeredEdges = {} #key: facehash value: dict of key:  edgehash value: edgeObjects
        self.topFacePlanes = {} #key: occurrenceHash value:topFace plane
        self.selectedFaces = {} #key: occurrenceHash value: dict of key: facehash value: faceObjects
        self.selectedEdges = {} #key: facehash value: dict of key:  edgehash value: edgeObjects
        self.timeLineGroups = {} #key: occurrenceHash value: timelineGroup object
        self.app = adsk.core.Application.get()
        self.design = self.app.activeProduct
        
    def addFace(self, face):
        self.logger.debug('registered Faces before = {}'.format(pformat(self.registeredFaces)))
        self.logger.debug('selected Faces before = {}'.format(pformat(self.selectedFaces)))
        self.logger.debug('registered Edges before = {}'.format(pformat(self.registeredEdges)))
        self.logger.debug('selected Edges before = {}'.format(pformat(self.selectedEdges)))
            
        faces = self.selectedFaces.setdefault(calcOccHash(face), {})
        self.logger.debug('cache cleared')
        self.completeEntityList.cache_clear()

        if faces:
            faceObject = self.registeredFaces[calcOccHash(face)][calcHash(face)]
            faceObject.selected = True
            return
        
        body = face.body
        faceNormal = dbUtils.getFaceNormal(face)
        
        for face1 in body.faces:
            if faceNormal.angleTo(dbUtils.getFaceNormal(face1))== 0:
                faceObject = SelectedFace(face1, self)

        self.logger.debug('registered Faces after = {}'.format(pformat(self.registeredFaces)))
        self.logger.debug('selected Faces after = {}'.format(pformat(self.selectedFaces)))
        self.logger.debug('registered Edges after = {}'.format(pformat(self.registeredEdges)))
        self.logger.debug('selected Edges after = {}'.format(pformat(self.selectedEdges)))
        
    def deleteFace(self, face):
#TODO:
        self.logger.debug('registered Faces before = {}'.format(pformat(self.registeredFaces)))
        self.logger.debug('selected Faces before = {}'.format(pformat(self.selectedFaces)))
        self.logger.debug('registered Edges before = {}'.format(pformat(self.registeredEdges)))
        self.logger.debug('selected Edges before = {}'.format(pformat(self.selectedEdges)))
        occHash = calcOccHash(face)
        face.attributes.itemByName(DBGROUP, DBFACE_REGISTERED).deleteMe()
        faceObject = self.registeredFaces[occHash][calcHash(face)]
        faceObject.selected = False
        if not self.selectedFaces[occHash]:
            self.remove(occHash)
        self.logger.debug('registered Faces after = {}'.format(pformat(self.registeredFaces)))
        self.logger.debug('selected Faces after = {}'.format(pformat(self.selectedFaces)))
        self.logger.debug('registered Edges after = {}'.format(pformat(self.registeredEdges)))
        self.logger.debug('selected Edges after = {}'.format(pformat(self.selectedEdges)))
        self.completeEntityList.cache_clear()
        self.logger.debug('cache cleared')
        
    def reinstateModel(self):
        registeredFacesAttribs = self.design.findAttributes(DBGROUP, 're:faceId.*')
        for faceAttrib in registeredFacesAttribs:
            keyName = faceAttrib.name.split(':')[0]
            occHash = faceAttrib.name[len(keyName)+1:]
            faceObject = ReinstateFace(faceAttrib.parent, occHash, self)

    def remove(self, occHash):
        for faceObject in self.registeredFaces[occHash].values():
            faceObject.face.attributes.itemByName(DBGROUP, DBFACE_REGISTERED).deleteMe()
            del self.registeredEdges[faceObject.faceHash]
            del self.selectedEdges[faceObject.faceHash]
        del self.registeredFaces[occHash]
        del self.selectedFaces[occHash]
        
    def addEdge(self, edge):
        edgeHash = calcHash(edge)
        for edgeList in self.registeredEdges.values():
            if calcHash(edge) in edgeList:
                edgeObject = edgeList[edgeHash]
                break
        self.logger.debug('cache cleared')
        self.completeEntityList.cache_clear()
        edgeObject.selected = True
        edgeObject.parent.selected = (True, False)
            
        
    def deleteEdge(self, edge):
        edgeHash = calcHash(edge)
        edge.attributes.itemByName(DBGROUP, DBEDGE_REGISTERED).deleteMe()
        for edgeList in self.registeredEdges.values():
            if calcHash(edge) in edgeList:
                edgeObject = edgeList[edgeHash]
                break
        edgeObject.selected = False
        if not edgeObject.selectedEdges:
            edgeObject.parent.selected = (False, False)
        if not edgeObject.parent.selectedFaces:
            self.remove(calcOccHash(edge))
            
    def clearAttribs(self, name):  #not used - can be called manually during debugging
        attribs = self.design.findAttributes(name, '')
        for attrib in attribs:
            attrib.deleteMe()
            
            
    def preLoad(self):
        timelineGroups = self.design.timeline.timelineGroups
        for tlGroup in timelineGroups:
            tlgName = tlGroup.name
            if 'db:' not in tlgName:
                continue
            occHash = tlgName[3:]
            tlGroup.isCollapsed = False
            featureBodyTLObject = tlGroup.item(0)
            featureBodyTLObject.isSuppressed = True
            self.timeLineGroups[occHash] = tlGroup
            self.registeredFaces[occHash] = {}
            bodyAttributes = self.design.findAttributes(DBGROUP, 'occId:'+occHash)
            for bodyAttribute in bodyAttributes:
                faceHashes = json.loads(bodyAttribute.value)
                for faceHash in faceHashes:
                    faceAttributes = self.design.findAttributes(DBGROUP, 'faceId:'+faceHash)
                    if not faceAttributes:
                        continue
                    for faceAttribute in faceAttributes:
                        if not faceAttribute.value:
                            continue
                        faceObject = SelectedFace(faceAttribute.parent, self, (faceHash, occHash))
#==============================================================================
#                         self.registeredFaces[occHash][faceHash] = faceAttribute.parent  #adds placeholder for face Object
#                         edgeHashList = json.loads(faceAttribute.value)
#                         for edgeHash in edgeHashList:
#                             self.registeredEdges[faceHash] = {}
#                             edgeAttributes = self.design.findAttributes(DBGROUP, 'edgeId:'+edgeHash)
#                             for edgeAttribute in edgeAttributes:
#                                 self.registeredEdges[faceHash][edgeHash] = edgeAttribute.parent
#                         faceObject = SelectedFace(face, self, faceHash = faceHash)
#==============================================================================

#==============================================================================
#         registeredEdgesAttribs = self.design.findAttributes(DBGROUP, 're:edgeId.*')
#         if not registeredFacesAttribs:
#             return
# #        if not registeredEdgesAttribs:
# #            return
#         for registeredFaceAttrib in registeredFacesAttribs:
#             occHash = registeredFaceAttrib.name.split(':')
#             occHash = occHash[1] + ':' + occHash[2]
#             self.addFace(registeredFaceAttrib.parent)
# #        for registeredEdgeAttrib in registeredEdgesAttribs:
# #            self.addEdge(registeredFaceAttrib.parent)
#         selectedFacesAttribs = self.design.findAttributes(DBGROUP, DBFACE_SELECTED)
#         selectedEdgesAttribs = self.design.findAttributes(DBGROUP, DBEDGE_SELECTED)
# #        if not selectedFacesAttribs:
# #            return
#         if not selectedEdgesAttribs:
#             return
# #        for selectedFaceAttrib in selectedFacesAttribs:
# #            self.addFace(selectedFaceAttrib.parent)
#         for selectedEdgeAttrib in selectedEdgesAttribs:
#             self.addEdge(selectedEdgeAttrib.parent)            
#         timelineGroups = self.design.timeline.timelineGroups
#         if not timelineGroups:
#==============================================================================
#            return False

    def refreshAttributes(self):
#TODO
        for occHash, facesDict in self.registeredFaces.items():
            occHashFlag = True
            for faceObject in facesDict.values():
                if occHashFlag:
                    faceObject.face.body.attributes.add(DBGROUP, 'occId:'+occHash, json.dumps(list(faceObject.registeredFaces.keys())))
                    occHashFlag = False
                faceObject.refreshAttributes()
    
    @lru_cache(maxsize=128)
    def completeEntityList(self, entityHash):  #needs hashable parameters in the arguments for lru_cache to work
        self.logger.debug('Entity Hash  = {}'.format(entityHash))
        return  (entityHash in map(lambda x: x.faceHash, self.registeredFaceObjectsAsList)) or (entityHash in map(lambda x: x.edgeHash, self.registeredEdgeObjectsAsList))

    def isSelectable(self, entity):
        
        self._entity = entity

        if entity.assemblyContext:
            self.activeoccurrenceHash = entity.assemblyContext.component.name
        else:
            self.activeoccurrenceHash = entity.body.name
        if self.activeoccurrenceHash not in map(lambda x: x.split(":")[0], self.registeredFaces.keys()):
            return True

        return self.completeEntityList(calcHash(entity))
        
    @property        
    def registeredEdgeObjectsAsList(self):
        edgeList = []
        for comp in self.registeredEdges.values():
            edgeList += comp.values()
        return edgeList

    @property        
    def registeredFaceObjectsAsList(self):
        faceList = []
        for comp in self.registeredFaces.values():
            faceList += comp.values()
        return faceList
        
    @property
    def registeredEntitiesAsList(self):
        self.logger.debug('registeredEntitiesAsList')
        edgeList = []
        for comp in self.registeredEdges.values():
            edgeList += comp
        faceList = []
        for comp in self.registeredFaces.values():
            faceList += comp
            faceEntities = map(lambda faceObjects: faceObjects.face, edgeList)
            edgeEntities = map(lambda edgeObjects: edgeObjects.edge, faceList)
            return faceEntities + edgeEntities
            
#    @property 
#    def selectedEdges(self):
#        self.logger.debug('selectedEdges called')
#        return self._selectedEdges


    @property
    def selectedEdgeObjectsAsList(self):
        self.logger.debug('selectedEdgesAsList')
        edgeList = []
        for comp in self.selectedEdges.values():
            edgeList += comp.values()
#            edges = copy.deepcopy(self.selectedEdges)
#        x =  map(lambda edgeObject: edgeObject.edge, reduce(operator.iadd, list(self.selectedEdges.values())))
        return edgeList
            
    @property        
    def selectedFaceObjectsAsList(self):
        self.logger.debug('selectedFacesAsList')

        faceList = []
        for comp in self.selectedFaces.values():
            faceList += comp.values()
            
#        x =  []+list(self.selectedFaces.values())
#            edges = copy.deepcopy(self.selectedEdges)
        return faceList
                     
    @property        
    def selectedEdgesAsGroupList(self):  # Grouped by occurrence/component
        self.logger.debug('registeredEdgeObjectsAsGroupList')
        groupedEdges  = {}
        for occurrence in self.selectedFaces:
            edges = []
            for faceHash in self.selectedFaces[occurrence]:
                edges += self.selectedEdges[faceHash].values()
            groupedEdges[occurrence] = edges
        return groupedEdges

class SelectedEdge:
    def __init__(self, edge, parentFace, parameters = False):
        self.logger = logging.getLogger('dogbone.mgr.edge')
        self.logger.info('---------------------------------{}---------------------------'.format('creating edge'))
        self.edge = edge
        self.edgeHash = calcHash(edge) if not parameters else parameters[0]
#            self.edgeParameters = json.loads(self.edge.attributes.itemByName(DBGROUP, 'edgeId:'+edgeHash))
        self.tempId = edge.tempId if not parameters else parameters[0].split(':')[0]
        self.parent = weakref.ref(parentFace)()
        self.selectedEdges = self.parent.selectedEdges
        self.selectedEdges[self.edgeHash] = self
        self.registeredEdges = self.parent.registeredEdges
        self.registeredEdges[self.edgeHash] = self
        self._selected = True 
        self.selected = True if parameters[1] else False#invokes selected property
        self.edge.attributes.add(DBGROUP, DBEDGE_REGISTERED, 'True')
        self.logger.debug('{} - edge initiated'.format(self.edgeHash))
        
    def __del__(self):
        self.logger.debug('edge {} deleted'.format(self.edgeHash))
        del self.registeredEdges[self.edgeHash]
        self.logger.debug('{} - edge deleted'.format(self.edgeHash))
        if not self.registeredEdges:
            del self.parent.registeredEdges[self.parent.faceHash]
            self.logger.debug('{} - registered edge dict deleted'.format(self.edgeHash))
            
    def refreshAttributes(self):
        self.edge.attributes.add(DBGROUP, 'edgeId:'+self.edgeHash, 'selected' if self.selected else '')
        
    @property
    def selected(self):
        return self._selected
        
    @selected.setter
    def selected(self, selected, dbType = 'Normal Dogbone'):
        self.logger.debug('{} - edge {}'.format(self.edgeHash, 'selected' if selected else 'deselected'))
        self.logger.debug('before selected edge count for face {} = {}'.format(self.parent.faceHash, len(self.selectedEdges)))
        if selected:
            self.selectedEdges[self.edgeHash] = self
            self.logger.debug('{} - edge appended to selectedEdges'.format(self.edgeHash))
            attr = self.edge.attributes.add(DBGROUP, DBEDGE_SELECTED, dbType)
        else: 
            del self.selectedEdges[self.edgeHash]
            self.logger.debug('{} - edge removed from selectedEdges'.format(self.edgeHash))
            self.edge.attributes.itemByName(DBGROUP, DBEDGE_SELECTED).deleteMe()
        self._selected = selected
        self.logger.debug('after selected edge count for face {} = {}'.format(self.parent.faceHash, len(self.selectedEdges)))
        
    def getAttributeValue(self):
        return self.face.attributes.itemByName(DBGROUP, 'edgeId:'+self.parent.faceHash).value

    def setAttributeValue(self, value):
        self.face.attributes.add(DBGROUP, 'edgeId:'+self.parent.faceHash, value)
        
    @property
    def topFacePlane(self):
        return self.parent.topFacePlane
        
class reinstateEdge(SelectedEdge):
#TODO

    def __init__(self, edgeAttribute, parent):
        
        keyId = edgeAttribute.name.split(':')[0]
        faceHash = edgeAttribute.name[len(keyId)+1:]
        edge = edgeAttribute.parent
        super().__init__(edge, parent, faceHash = faceHash)
        

class SelectedFace:
    """
    This class manages a single Face:
    keeps a record of the viable edges, whether selected or not
    principle of operation: the first face added to a body/occurrence entity will find all other same facing faces, automatically finding eligible edges - they will all be selected by default.
    edges and faces have to be selected to be selected in the UI
    when all edges of a face have been deselected, the face becomes deselected
    when all faces of a body/entity have been deselected - the occurrence and all associated face and edge objects will be deleted and GC'd
    manages edges:
        edges can be selected or deselected individually
        faces can be selected or deselected individually
        first face selection will cause all other appropriate faces and corresponding edges on the body to be selected
        validEdges dict makes lists of candidate edges available
        each face or edge selection that is changed will reflect in the parent management object selectedOccurrences, selectedFaces and selectedEdges
    """

    def __init__(self, face, parent, preloading = False):
        self.logger = logging.getLogger('dogbone.mgr.edge')

        self.logger.info('---------------------------------{}---------------------------'.format('creating face'))
        
        self.face = face # BrepFace
        
        self.faceHash = calcHash(face) if not preloading else preloading[0]


        self.tempId = face.tempId if preloading else preloading[0].split(':')[0]
        self._selected = True # record of all valid faces are kept, but only ones that are selected==True are processed for dogbones???
        self.parent = weakref.ref(parent)()

        self.occurrenceHash = calcOccHash(face) if preloading else preloading[1]
        self.topFacePlane = self.parent.topFacePlanes.setdefault(self.occurrenceHash, dbUtils.getTopFacePlane(face))
        
        self.registeredFaces = self.parent.registeredFaces.setdefault(self.occurrenceHash, {})
        self.registeredFaces[self.faceHash] = self
        self.registeredEdges = self.parent.registeredEdges.setdefault(self.faceHash, {})
        
        self.selectedFaces = self.parent.selectedFaces.setdefault(self.occurrenceHash, {})
        self.selectedEdges = self.parent.selectedEdges.setdefault(self.faceHash, {})
        self.logger.debug('{} - face initiated'.format(self.faceHash))           

        #==============================================================================
        #             this is where inside corner edges, dropping down from the face are processed
        #==============================================================================
        
        if preloading:
            faceAttribute = self.face.attributes.itemByName(DBGROUP, 'faceId:'+self.faceHash)
            edgeHashes = json.loads(faceAttribute.value)
            for edgeHash in edgeHashes:
                edgeAttributes = parent.design.findAttributes(DBGROUP, 'edgeId:'+edgeHash)
                self.selected = (True, False) if len(edgeAttributes)>0 else (False, False)
                for edgeAttribute in edgeAttributes:
                    edge = edgeAttribute.parent
                    edgeObject = SelectedEdge(edge, self, parameters = (edgeHash, edgeAttribute.value))
            return
            
        
        self.brepEdges = dbUtils.findInnerCorners(face) #get all candidate edges associated with this face
        if not self.brepEdges:
            self.logger.debug('no edges found on selected face '.format(self.faceHash))
            self._selected = False
            return

        self.logger.debug('{} - edges found on face creation'.format(len(self.brepEdges)))
        for edge in self.brepEdges:
            if edge.isDegenerate:
                continue
            try:
                
                edgeObject = SelectedEdge(edge, self)
                self.logger.debug(' {} - edge object added'.format(edgeObject.edgeHash))
    
            except:
                dbUtils.messageBox('Failed at edge:\n{}'.format(traceback.format_exc()))
        self.selected = True
        self.logger.debug('registered component count = {}'.format(len(self.parent.registeredFaces.keys())))
                
    def __del__(self):
        self.logger.debug("face {} deleted".format(self.faceHash))
        del self.registeredFaces[self.faceHash]
        del self.selectedFaces[self.faceHash]
        self.logger.debug(' {} - face key deleted from registeredFaces'.format(self.faceHash))
        self.logger.debug('registered component count = {}'.format(len(self.parent.registeredFaces)))
        for edgeObject in self.registeredEdges.values():
            del edgeObject
            self.logger.debug(' {} - edge object deleted'.format(edgeObject.edgeHash))
        del self.parent.registeredEdges[self.faceHash]
        self.logger.debug('registered Faces count = {}'.format(len(self.registeredFaces)))               
        self.logger.debug('selected Faces count = {}'.format(len(self.selectedFaces)))               
        self.logger.debug('registered edges count = {}'.format(len(self.registeredEdges)))               
        self.logger.debug('selected edges count = {}'.format(len(self.selectededEdges)))
        
    def refreshAttributes(self):
        self.face.attributes.add(DBGROUP, 'faceId:'+self.faceHash, json.dumps(list(self.registeredEdges.keys())) if self.selected else '')
        for edgeObject in self.registeredEdges.values():
            edgeObject.refreshAttributes()

    def getAttributeValue(self):
        return self.face.attributes.itemByName(DBGROUP, 'faceId:'+self.occurrenceHash).value

    def setAttributeValue(self):
        
        self.face.attributes.add(DBGROUP, 'faceId:'+self.occurrenceHash, value)

    @property
    def selected(self):
        return self._selected
        
    @selected.setter
    def selected(self, selected, dbType = 'Normal Dogbone'):
        allEdges = True
        if isinstance(selected, tuple):
            allEdges = selected[1]
            selected = selected[0]
        self._selected = selected
        if not selected:
            del self.selectedFaces[self.faceHash]
            attr = self.face.attributes.itemByName(DBGROUP, DBFACE_SELECTED).deleteMe()

            self.logger.debug(' {} - face object removed from selectedFaces'.format(self.faceHash))
        else:
            self.selectedFaces[self.faceHash] = self
#            attr = self.face.attributes.add(DBGROUP, DBFACE_SELECTED, dbType)
            self.logger.debug(' {} - face object added to registeredFaces'.format(self.faceHash))

        if allEdges:
            self.logger.debug('{} all edges after face {}'.format('Selecting' if selected else 'Deselecting', 'Selected' if selected else 'Deselected'))
            for edge in self.registeredEdges.values():
                try:
                    edge.selected = selected
                except:
                    continue
            self.logger.debug(' {} - edge object {}'.format(edge.edgeHash, 'selected' if selected else 'deselected'))
        self._selected = selected